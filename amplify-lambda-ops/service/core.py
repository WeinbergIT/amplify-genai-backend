import os
import uuid
import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from common.validate import validated
from common.ops import op
from common.auth_admin import verify_user_as_admin

dynamodb = boto3.client("dynamodb")




@validated(op="get")
def get_all_ops(event, context, current_user, name, data):
    if (not verify_user_as_admin(data['access_token'], 'Get All Ops')):
        return {'success': False , 'error': 'Unable to authenticate user as admin'}

    return fetch_user_ops("system", 'all')

@op(
    path="/ops/get",
    tags=["ops", "default"],
    name="getOperations",
    description="Get a list of available operations for an assistant.",
    params={
        "tag": "The optional tag to search for.",
    },
)
@validated(op="get")
def get_all_ops(event, context, current_user, name, data):
    if not verify_user_as_admin(data["access_token"], "Get All Ops"):
        return {"success": False, "error": "Unable to authenticate user as admin"}

    return fetch_user_ops("system", "all")


@validated(op="get")
def get_ops(event, context, current_user, name, data):
    data = data["data"]
    # Get the 'tag' parameter from the request data
    tag = data.get("tag", "default")
    return fetch_user_ops(current_user, tag)


def fetch_user_ops(current_user, tag):
    # Get the DynamoDB table name from the environment variable
    table_name = os.environ.get("OPS_DYNAMODB_TABLE")

    print(f"Finding operations for user {current_user} with tag {tag}")

    # Build the DynamoDB query parameters
    query_params = {
        "TableName": table_name,
        "KeyConditionExpression": "#usr = :user AND tag = :tag",
        "ExpressionAttributeValues": {":user": {"S": current_user}, ":tag": {"S": tag}},
        "ExpressionAttributeNames": {"#usr": "user"},
    }
    
    all_items = []
    last_evaluated_key = None
    
    # Loop to handle pagination
    while True:
        # Add the ExclusiveStartKey if we have a LastEvaluatedKey from previous query
        if last_evaluated_key:
            query_params['ExclusiveStartKey'] = last_evaluated_key
            
        # Execute the DynamoDB query
        response = dynamodb.query(**query_params)
        
        # Add current batch to our results
        all_items.extend(response['Items'])
        
        # Check if there are more results
        last_evaluated_key = response.get('LastEvaluatedKey')
        if not last_evaluated_key:
            break
    
    # Extract the data from the DynamoDB response
    data_from_dynamo = [item["ops"] for item in response["Items"]]
    data_from_dynamo = [
        TypeDeserializer().deserialize(item) for item in data_from_dynamo
    ]
    # Flatten the list of operations
    data_from_dynamo = [op for sublist in data_from_dynamo for op in sublist]

    print(f"Found operations {data_from_dynamo} for user {current_user} with tag {tag}")

    if current_user != "system":
        try:
            system_ops = fetch_user_ops("system", tag)
            print(f"System operations: {system_ops}")
            system_ops = system_ops["data"]
            data_from_dynamo.extend(system_ops)
        except Exception as e:
            print(f"Failed to retrieve system operations: {e}")

    return {
        "success": True,
        "message": "Successfully retrieved available operations for user",
        "data": data_from_dynamo,
    }


@validated(op="write")
def write_ops(event, context, current_user, name, data):
    ops = data["data"]["ops"]
    user = "system"  # for now

    table_name = os.environ.get("OPS_DYNAMODB_TABLE")
    if not table_name:
        return {
            "success": False,
            "message": "DynamoDB table name is not set in environment variables",
        }

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    # Validate and Serialize operations for DynamoDB
    for op in ops:
        if "tag" in op:
            del op["tag"]

        print(op)
        op["includeAccessToken"] = True
        # Check and register based on tags attached to the operation
        operation_tags = op.get("tags", ["default"])
        operation_tags.append("all")

        for tag in operation_tags:
            # Check if an entry exists
            response = table.query(
                KeyConditionExpression=Key("user").eq(user) & Key("tag").eq(tag)
            )
            existing_items = response["Items"]

            if existing_items:
                # If an entry exists, update it by checking for op id
                for item in existing_items:
                    existing_ops = item["ops"]
                    op_exists = False

                    for index, existing_op in enumerate(existing_ops):
                        if existing_op["id"] == op["id"]:
                            existing_ops[index] = op
                            op_exists = True
                            break

                    if not op_exists:
                        existing_ops.append(op)

                    table.update_item(
                        Key={
                            "user": user,
                            "tag": tag,
                        },
                        UpdateExpression="SET ops = :ops",
                        ExpressionAttributeValues={
                            ":ops": existing_ops,
                        },
                    )

            else:
                item = {
                    "id": str(uuid.uuid4()),  # Using UUID to ensure unique primary key
                    "user": user,
                    "tag": tag,
                    "ops": [op],
                }
                table.put_item(Item=item)

    return {
        "success": True,
        "message": "Successfully associated operations with provided tags and user",
    }


@validated(op="delete")
def delete_op(event, context, current_user, name, data):
    op = data["data"]["op"]
    tags = op.get("tags", ["default"])
    if "all" not in tags:
        tags.append("all")
    user = "system"

    table_name = os.environ.get("OPS_DYNAMODB_TABLE")
    if not table_name:
        return {
            "success": False,
            "message": "DynamoDB table name is not set in environment variables",
        }

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    deleted_any = False
    for tag in op["tags"]:
        print(tag)

        response = table.query(
            KeyConditionExpression=Key("user").eq(user) & Key("tag").eq(tag)
        )
        existing_items = response["Items"]

        for item in existing_items:
            existing_ops = item.get("ops", [])

            filtered_ops = []
            for existing_op in existing_ops:
                # Only keep ops that do NOT match all three attributes
                if not (
                    existing_op.get("id") == op["id"]
                    and existing_op.get("name") == op["name"]
                    and existing_op.get("url") == op["url"]
                ):
                    filtered_ops.append(existing_op)
                else:
                    deleted_any = True

                    # Update if something changed
            if len(filtered_ops) != len(existing_ops):
                if filtered_ops:
                    # If there are still ops left, update the item
                    table.update_item(
                        Key={
                            "user": user,
                            "tag": tag,
                        },
                        UpdateExpression="SET ops = :ops",
                        ExpressionAttributeValues={
                            ":ops": filtered_ops,
                        },
                    )
                else:
                    # If no ops remain, delete the entire item
                    table.delete_item(Key={"user": user, "tag": tag})

    if not deleted_any:
        print("No matching operation(s) found to delete")
    return {
        "success": True,
        "message": "Successfully deleted the specified operation(s)",
    }
