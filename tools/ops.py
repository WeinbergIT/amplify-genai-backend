import os
import ast
from typing import List

from boto3.dynamodb.conditions import Key
from pydantic import BaseModel, field_validator
import os
import uuid
import boto3
from typing import List
from boto3.dynamodb.types import TypeSerializer
from pydantic import ValidationError
import os
import sys
import yaml
import argparse
from typing import Optional

dynamodb = boto3.resource("dynamodb")
serializer = TypeSerializer()

IGNORED_DIRECTORIES = {"node_modules", "venv", "__pycache__"}


def op(
    tags=None,
    path="",
    name="",
    description="",
    params=None,
    method="POST",
    parameters=None,
):
    # This is the actual decorator
    def decorator(func):
        def wrapper(*args, **kwargs):
            # You can do something with tags, name, description, and params here
            print(f"Path: {path}")
            print(f"Tags: {tags}")
            print(f"Name: {name}")
            print(f"Method: {method}")
            print(f"Description: {description}")
            print(f"Params: {params}")
            # Call the actual function
            result = func(*args, **kwargs)
            return result

        return wrapper

    return decorator


# Pydantic models
class ParamModel(BaseModel):
    description: str
    name: str


class OperationModel(BaseModel):
    description: str
    id: str
    includeAccessToken: bool
    method: str
    name: str
    tags: List[str]
    params: List[ParamModel]
    type: str
    url: str
    schema: dict = None  # Add schema field to store JSON schema

    @field_validator("method")
    def validate_method(cls, v):
        allowed_methods = {"GET", "POST", "PUT", "DELETE", "PATCH"}
        if v.upper() not in allowed_methods:
            raise ValueError(f"Method must be one of {allowed_methods}")
        return v.upper()


def extract_dict_from_ast(node):
    """Helper function to extract dictionary from AST nodes"""
    if isinstance(node, ast.Dict):
        keys = []
        values = []
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant):
                key = k.value
            elif isinstance(k, ast.Str):  # for older Python versions
                key = k.s
            else:
                continue

            if isinstance(v, ast.Dict):
                value = extract_dict_from_ast(v)
            elif isinstance(v, ast.List):
                value = [
                    x.value if isinstance(x, ast.Constant) else x.s for x in v.elts
                ]
            elif isinstance(v, ast.Constant):
                value = v.value
            elif isinstance(v, ast.Str):  # for older Python versions
                value = v.s
            else:
                continue

            keys.append(key)
            values.append(value)
        return dict(zip(keys, values))
    return {}


def find_python_files(directory: str) -> List[str]:
    python_files = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRECTORIES]
        for file in files:
            if file.endswith(".py"):
                python_files.append(os.path.join(root, file))
    return python_files


def extract_dict(ast_node):
    """Extract dictionary from AST Dict node."""
    return {key.s: value.s for key, value in zip(ast_node.keys, ast_node.values)}

def extract_complex_dict(ast_node):
    """Extract nested dictionary structures from AST Dict node."""
    result = {}
    for key, value in zip(ast_node.keys, ast_node.values):
        if isinstance(value, ast.Dict):
            result[key.s] = extract_complex_dict(value)
        elif isinstance(value, ast.List):
            result[key.s] = extract_list(value)
        elif isinstance(value, ast.Constant):
            result[key.s] = value.value
        elif isinstance(value, ast.Str):
            result[key.s] = value.s
        else:
            # Try to get a literal value or default to string representation
            try:
                result[key.s] = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                result[key.s] = str(value)
    return result

def extract_list(ast_node):
    """Extract list from AST List node."""
    result = []
    for item in ast_node.elts:
        if isinstance(item, ast.Dict):
            result.append(extract_complex_dict(item))
        elif isinstance(item, ast.List):
            result.append(extract_list(item))
        elif isinstance(item, ast.Constant):
            result.append(item.value)
        elif isinstance(item, ast.Str):
            result.append(item.s)
        else:
            # Try to get a literal value or default to string representation
            try:
                result.append(ast.literal_eval(item))
            except (ValueError, SyntaxError):
                result.append(str(item))
    return result

def extract_tags(op_kwargs):
    tags = op_kwargs.get("tags", [])

    # Ensure tags is a list
    if isinstance(tags, ast.List):
        # Extract elements from the ast.List
        tags = [elt.s if isinstance(elt, ast.Str) else str(elt) for elt in tags.elts]

    return tags if isinstance(tags, list) else []


def extract_ops_from_file(file_path: str) -> List[OperationModel]:
    try:
        ops_found = []
        with open(file_path, "r") as file:
            content = file.read()

        # Parse the abstract syntax tree of the file
        tree = ast.parse(content)

        # Look for function definitions and their decorators
        for node in ast.walk(tree):
            try:
                if isinstance(node, ast.FunctionDef):
                    for decorator in node.decorator_list:
                        if isinstance(decorator, ast.Call) and (
                            getattr(decorator.func, "id", None) == "op"
                            or getattr(decorator.func, "id", None) == "vop"
                        ):
                            op_kwargs = {kw.arg: kw.value for kw in decorator.keywords}
                            if (
                                "path" in op_kwargs
                                and "name" in op_kwargs
                                and "description" in op_kwargs
                                and ("params" in op_kwargs or "parameters" in op_kwargs)
                            ):
                                params_dict = extract_dict(op_kwargs["params"])

                                params = (
                                    [
                                        ParamModel(description=desc, name=name)
                                        for name, desc in params_dict.items()
                                    ]
                                    if "params" in op_kwargs
                                    else []
                                )
                                parameters = extract_dict_from_ast(
                                    op_kwargs.get(
                                        "parameters", ast.Dict(keys=[], values=[])
                                    )
                                )
                                try:
                                    operation = OperationModel(
                                        description=op_kwargs["description"].s,
                                        id=op_kwargs["name"].s,
                                        includeAccessToken=True,  # Assuming ops will include access token
                                        method=(
                                            op_kwargs["method"].s
                                            if "method" in op_kwargs
                                            else "POST"
                                        ),  # Default method
                                        name=op_kwargs["name"].s,
                                        params=params,
                                        type="custom",  # Assuming custom type
                                        url=op_kwargs["path"].s,
                                        tags=extract_tags(op_kwargs),
                                        parameters=parameters,
                                    )
                                    ops_found.append(operation)
                                except ValidationError as ve:
                                    print(f"\nValidation error in {file_path}:")
                                    for error in ve.errors():
                                        print(f"Parsing: " + op_kwargs["name"].s)
                                        print(
                                            f"Field: {' -> '.join(str(x) for x in error['loc'])}"
                                        )
                                        print(f"Error: {error['msg']}")
                                        print(f"Type: {error['type']}\n")
            except Exception as e:
                print(f"Error processing function {node.name} in {file_path}: {e}")

        return ops_found
    except Exception as e:
        print(e)
        print(f"Skipping {file_path} due to unparseable AST")
        return []


def scan_and_register_ops(
    path="./", current_user: str = "system", tags: List[str] = None
):
    all_ops = scan_ops(path)
    response = write_ops(current_user=current_user, tags=tags, ops=all_ops)
    print(response)


def print_pretty_ops(ops: List[OperationModel]):
    for op in ops:
        print("Operation Details:")
        print(f"  Name       : {op.name}")
        print(f"  URL        : {op.url}")
        print(f"  Method     : {op.method}")
        print(f"  Description: {op.description}")
        print(f"  ID         : {op.id}")
        print("  Params:")
        for param in op.params:
            print(f"    - {param.name} : {param.description}")
        print(f"  Include Access Token: {op.includeAccessToken}")
        print(f"  Type       : {op.type}")
        print("")


def scan_ops(path=".") -> List[OperationModel]:
    python_files = find_python_files(path)
    all_ops = []

    for file_path in python_files:
        file_ops = extract_ops_from_file(file_path)
        all_ops.extend(file_ops)

    return all_ops


def scan_and_print_ops(path="."):
    all_ops = scan_ops(path)
    print_pretty_ops(all_ops)


def write_ops(
    current_user: str = "system",
    tags: List[str] = None,
    ops: List[OperationModel] = None,
):
    print_pretty_ops(ops)

    # Get the DynamoDB table name from the environment variable
    table_name = os.environ.get("OPS_DYNAMODB_TABLE")
    if not table_name:
        return {
            "success": False,
            "message": "DynamoDB table name is not set in environment variables",
        }

    # Use a resource client to interact with DynamoDB
    table = dynamodb.Table(table_name)

    # Check if `ops` is provided
    if ops is None:
        return {"success": False, "message": "Operations must be provided"}

    # Validate and Serialize operations for DynamoDB
    for op in ops:
        try:
            op_dict = op.model_dump()
        except ValidationError as e:
            return {"success": False, "message": f"Operation validation failed: {e}"}

        # Check and register based on tags attached to the operation
        operation_tags = op_dict.get("tags", ["default"])
        operation_tags.append("all")

        for tag in operation_tags:
            # Check if an entry exists
            response = table.query(
                KeyConditionExpression=Key("user").eq(current_user) & Key("tag").eq(tag)
            )
            existing_items = response["Items"]

            if existing_items:
                # If an entry exists, update it by checking for op id
                for item in existing_items:
                    existing_ops = item["ops"]
                    op_exists = False

                    for index, existing_op in enumerate(existing_ops):
                        if existing_op["id"] == op_dict["id"]:
                            print(
                                f"Updating {op_dict['id']} for user {current_user} and tag {tag}"
                            )
                            print(f"Operation: {json.dumps(op_dict, indent=2)}")
                            existing_ops[index] = op_dict
                            op_exists = True
                            break

                    if not op_exists:
                        existing_ops.append(op_dict)

                    table.update_item(
                        Key={
                            "user": current_user,
                            "tag": tag,
                        },
                        UpdateExpression="SET ops = :ops",
                        ExpressionAttributeValues={
                            ":ops": existing_ops,
                        },
                    )
                    print(
                        f"Published operation with id {op.id} to table {table_name} for user {current_user} and tag {tag}: {op_dict}"
                    )
            else:
                # If no entry exists, create a new one
                item = {
                    "id": str(uuid.uuid4()),  # Using UUID to ensure unique primary key
                    "user": current_user,
                    "tag": tag,
                    "ops": [op_dict],
                }
                table.put_item(Item=item)
                print(
                    f"Published operation with id {op.id} to table {table_name} for user {current_user} and tag {tag}: {op_dict}"
                )

    return {
        "success": True,
        "message": "Successfully associated operations with provided tags and user",
    }


def resolve_ops_table(stage: Optional[str], ops_table: Optional[str]) -> Optional[str]:
    if ops_table:
        return ops_table

    env_ops_table = os.environ.get("OPS_DYNAMODB_TABLE")
    if env_ops_table:
        return env_ops_table

    if stage:
        current_dir = os.getcwd()
        var_file_name = f"{stage}-var.yml"

        # Search for var file in the current directory or one directory up
        for _ in range(2):
            var_file_path = os.path.join(current_dir, "var", var_file_name)
            if os.path.exists(var_file_path):
                with open(var_file_path, "r") as file:
                    config = yaml.safe_load(file)
                return config.get("OPS_DYNAMODB_TABLE")

            # Check one directory up
            current_dir = os.path.abspath(os.path.join(current_dir, os.pardir))

    return None


def main():
    parser = argparse.ArgumentParser(description="Ops management script")
    parser.add_argument("--stage", type=str, help="The staging environment")
    parser.add_argument("--dir", type=str, help="The directory to search for ops")
    parser.add_argument(
        "--ops_table", type=str, help="The value for OPS_DYNAMODB_TABLE"
    )
    parser.add_argument(
        "command", choices=["ls", "register"], help="Command to execute"
    )

    args = parser.parse_args()

    # Execute the command
    if args.command == "ls":
        scan_and_print_ops(args.dir or ".")
    elif args.command == "register":
        # Resolve the DynamoDB table name
        ops_table = resolve_ops_table(args.stage, args.ops_table)
        if not ops_table:
            print(
                "Error: OPS_DYNAMODB_TABLE could not be resolved. Add it to your var/<stage>-var.yml file or set it "
                "as an environment variable or pass it with --ops_table <table_name>."
            )
            sys.exit(1)

        # Set the environment variable for DynamoDB operations
        os.environ["OPS_DYNAMODB_TABLE"] = ops_table
        scan_and_register_ops(args.dir or ".", current_user="system")


if __name__ == "__main__":
    main()
