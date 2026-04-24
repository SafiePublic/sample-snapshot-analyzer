import argparse
import io
import json
import logging
import os
import uuid
from datetime import datetime, timezone

import grpc
from google.protobuf.json_format import MessageToDict
from PIL import Image

from proto.snapshot.v1.analyzer_pb2 import AnalyzeRequest
from proto.snapshot.v1.analyzer_pb2_grpc import AnalyzerServiceStub
from tools.validator import (
    validate_context,
    validate_device_status,
    validate_event,
    validate_metrics,
    validate_object,
)

_GRPC_PORT = 50051


class InputFile:
    name: str
    content_type: str
    data: bytes

    def __init__(self, name, content_type, data):
        self.name = name
        self.content_type = content_type
        self.data = data


def request(
    server_host: str,
    server_port: int,
    image: InputFile,
    device_id: str,
    params: dict | None = None,
    context: dict | None = None,
    request_id: str | None = None,
    timestamp: datetime | None = None,
):
    req = AnalyzeRequest()

    request_id = request_id or str(uuid.uuid4())

    req.images.add()
    if not timestamp:
        req.images[0].timestamp.FromDatetime(datetime.now(timezone.utc))
    else:
        req.images[0].timestamp.FromDatetime(timestamp)
    req.images[0].name = image.name
    req.images[0].content_type = image.content_type
    req.images[0].data = image.data
    if params:
        for p in params:
            if isinstance(params[p], dict):
                req.parameter[p].update(params[p])
            else:
                raise Exception("Invalid parameter is specified")

    with grpc.insecure_channel(f"{server_host}:{server_port}") as channel:
        logging.debug(f"Request body: {req}")
        logging.info(f"Requesting to {server_host}:{server_port}")
        stub = AnalyzerServiceStub(channel)
        response = stub.Analyze(
            req,
            metadata=[
                ("request_id", request_id),
                ("device_id", device_id),
                ("context", json.dumps(context) if context else "{}"),
            ],
        )

    return response


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--host",
        type=str,
        default="localhost",
        help="gRPC server hostname ('localhost' by default).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=os.getenv("ANALYZER_PORT", _GRPC_PORT),
        help=f"gRPC server port ({_GRPC_PORT} or ANALYZER_PORT env variables is used by default).",
    )
    parser.add_argument(
        "-i",
        "--image",
        type=str,
        help="Path of the input image file. JPEG image is only supported.",
    )
    parser.add_argument(
        "-r",
        "--request-id",
        type=str,
        help="Request ID to trace the request on the analyzer (Generated automatically by default).",
    )
    parser.add_argument(
        "-t",
        "--timestamp",
        type=str,
        help="Timestamp of the input image (RFC3339 date-time format).",
    )
    parser.add_argument(
        "-u",
        "--user-config",
        type=str,
        default=None,
        help="Path to user_config JSON file",
    )
    parser.add_argument(
        "-d",
        "--developer-config",
        type=str,
        default=None,
        help="Path to developer_config JSON file",
    )
    parser.add_argument(
        "-g",
        "--geometry-config",
        type=str,
        default=None,
        help="Path to geometry_config JSON file",
    )
    parser.add_argument("-c", "--context", type=str, default=None, help="Path to context JSON file")
    parser.add_argument("--debug", action="store_true", help="Output debug logging in stderr.")
    parser.add_argument(
        "--device-id", type=str, default="test-device-1", help="Device ID to be sent to the server"
    )

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        )
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")

    # Load input image from the path
    image_name = os.path.basename(args.image)
    with open(args.image, "rb") as file:
        image_bytes = file.read()
        with Image.open(io.BytesIO(image_bytes), formats=["JPEG"]) as img:
            image_type = "image/jpeg"
    image = InputFile(name=image_name, content_type=image_type, data=image_bytes)

    if args.timestamp:
        timestamp = datetime.fromisoformat(args.timestamp.replace("Z", "+00:00"))
    else:
        timestamp = None

    # パラメータ情報の生成
    user_config_dict = {}
    if args.user_config is not None:
        with open(args.user_config, "r") as f:
            user_config_dict = json.loads(f.read())

    developer_config_dict = {}
    if args.developer_config is not None:
        with open(args.developer_config, "r") as f:
            developer_config_dict = json.loads(f.read())

    geometry_config_list = []
    if args.geometry_config is not None:
        with open(args.geometry_config, "r") as f:
            geometry_config_list = json.loads(f.read())

    if geometry_config_list:
        user_config_dict["geometries"] = geometry_config_list

    parameter = {}
    parameter["user_config"] = user_config_dict
    parameter["developer_config"] = developer_config_dict

    context_dict = {}
    if args.context is not None:
        with open(args.context, "r") as f:
            context_dict = json.loads(f.read())

    r = request(
        server_host=args.host,
        server_port=args.port,
        image=image,
        device_id=args.device_id,
        request_id=args.request_id,
        params=parameter,
        timestamp=timestamp,
        context=context_dict,
    )

    if r.HasField("record_metrics"):
        metrics = validate_metrics(MessageToDict(r.record_metrics, preserving_proto_field_name=True))
        logging.info("  metrics: %s", [m.model_dump() for m in metrics])

    if r.HasField("record_event"):
        event = validate_event(MessageToDict(r.record_event, preserving_proto_field_name=True))
        logging.info("  event: %s", event.model_dump())

    if r.HasField("record_object"):
        object = validate_object(MessageToDict(r.record_object, preserving_proto_field_name=True))
        logging.info("  object: %s", object.model_dump())

    if r.HasField("record_device_status"):
        device_status = validate_device_status(
            MessageToDict(r.record_device_status, preserving_proto_field_name=True)
        )
        logging.info(
            "  device status: %s",
            [d.model_dump() for d in device_status],
        )
    if r.HasField("update_context"):
        validate_context(r.update_context)
        logging.info(
            "  update context: %s",
            MessageToDict(r.update_context, preserving_proto_field_name=True),
        )
