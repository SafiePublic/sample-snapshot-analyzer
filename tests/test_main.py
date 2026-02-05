import io
from datetime import datetime, timezone
from unittest.mock import create_autospec

import grpc
import pytest
from PIL import Image

from analyzer.main import Analyzer
from proto.snapshot.v1.analyzer_pb2 import AnalyzeRequest


@pytest.fixture
def test_servicer():
    return Analyzer()


@pytest.fixture
def test_request():
    req = AnalyzeRequest()
    width = 400
    height = 300
    img = Image.new(mode="RGB", size=(width, height))
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="JPEG")
    img_bytes = img_bytes.getvalue()
    req.images.add()
    req.images[0].name = "test_image.jpg"
    req.images[0].content_type = "image/jpeg"
    req.images[0].timestamp.FromDatetime(datetime.now(timezone.utc))
    req.images[0].data = img_bytes

    return req


@pytest.fixture
def mock_context():
    return create_autospec(spec=grpc.ServicerContext)


def test_analyze(test_servicer, test_request, mock_context):
    r = test_servicer.Analyze(request=test_request, context=mock_context)
    assert r.HasField("record_metrics")
    assert r.HasField("record_event")
    assert r.HasField("record_object")
    assert r.HasField("record_device_status")
