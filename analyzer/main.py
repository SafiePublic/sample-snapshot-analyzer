import argparse
import concurrent.futures
import datetime
import io
import json
import logging
import os
import threading
from collections import Counter

import grpc
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Struct
from google.protobuf.timestamp_pb2 import Timestamp
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from proto.snapshot.v1.analyzer_pb2 import (
    AnalyzeRequest,
    AnalyzeResponse,
    DeviceStatus,
    EventPicture,
    ObjectPicture,
)
from proto.snapshot.v1.analyzer_pb2_grpc import (
    AnalyzerServiceServicer,
    add_AnalyzerServiceServicer_to_server,
)

_GRPC_PORT = 50051


class FrameAnalyzerResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    timestamp: Timestamp = Field(default_factory=Timestamp)
    thumbnail_data: bytes = b""
    labels: list[str] = Field(
        default_factory=list
    )  # ここに設定されたラベルでAI Studio画面上でフィルタリングが可能
    data: dict = Field(default_factory=dict)  # 任意のJSONシリアライズ可能なデータ
    score: float = 0.0  # ここに設定されたスコアでAI Studio画面上でフィルタリングが可能
    device_context: dict = Field(default_factory=dict)  # デバイスコンテキスト


class DummyObjectDetector:
    """ダミーの物体検出器"""

    def __init__(self, model_path: str) -> None:
        # NOTE: ここではコンストラクタでモデルをロードする想定でダミーを実装します。
        logging.info("Loaded dummy object detector model from %s", model_path)
        # self.model = load_model(model_path)

    def detect(self, image: Image.Image) -> list[dict[str, object]]:
        # NOTE: ここでは常に同じ物体を検出するダミーを実装します。
        # detections = self.model.predict(image)
        detections = [
            {
                "label": "person",
                "score": 0.9,
                "top_x": 50,
                "top_y": 100,
                "bottom_x": 150,
                "bottom_y": 300,
            },
            {
                "label": "car",
                "score": 0.8,
                "top_x": 300,
                "top_y": 200,
                "bottom_x": 500,
                "bottom_y": 400,
            },
        ]
        return detections


class FrameAnalyzer:
    """アプリケーション固有の画像解析ロジックを実装します。

    このサンプルでは、ダミー推論およびサムネイル画像を生成します。
    """

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self.object_detector: DummyObjectDetector = DummyObjectDetector("dummy_model_path")

    def convert_params(
        self,
        device_context: str | bytes | None = None,
        parameters: str | bytes | None = None,
    ) -> tuple[dict, dict, dict]:
        """パラメータを受け取って、デバイスコンテキストとユーザー設定とディベロッパー設定に変換して返します。

        Args:
            device_context (str | bytes | None, optional): デバイスコンテキスト. Defaults to None.
            parameters (str | bytes | None, optional): パラメータ. Defaults to None.

        Returns:
            tuple[dict, dict, dict]: デバイスコンテキスト、ユーザー設定、ディベロッパー設定
        """
        device_context_dict = {}
        user_conf = {}
        dev_conf = {}
        if device_context is not None:
            try:
                device_context_dict = json.loads(device_context)
            except json.JSONDecodeError:
                logging.warning("Failed to parse device_context as JSON.")
        if parameters is not None:
            try:
                params = json.loads(parameters)
                user_conf = params.get("user_config", {})
                dev_conf = params.get("developer_config", {})
            except json.JSONDecodeError:
                logging.warning("Failed to parse parameters as JSON.")
        return device_context_dict, user_conf, dev_conf

    def analyze_image(
        self,
        image: Image.Image,
        ts: Timestamp,
        device_id: str | bytes | None = None,
        device_context: str | bytes | None = None,
        parameters: str | bytes | None = None,
    ) -> FrameAnalyzerResult | None:
        """画像を解析して結果を返します。

        Args:
            image (Image.Image): 画像
            ts (Timestamp): タイムスタンプ
            device_id (str | bytes | None, optional): デバイスID. Defaults to None.
            device_context (str | bytes | None, optional): デバイスコンテキスト. Defaults to None.
            parameters (str | bytes | None, optional): パラメータ. Defaults to None.

        Returns:
            FrameAnalyzerResult | None: 解析結果
        """
        # NOTE: デバイスID、デバイスコンテキスト、パラメータなどを用いて解析処理を変更できます
        logging.info(
            "FrameAnalyzer got device_id=%s, device_context=%s, parameters=%s",
            device_id,
            device_context,
            parameters,
        )
        # パラメータを変換
        device_context_dict, user_config, developer_config = self.convert_params(device_context, parameters)
        detections = []
        # NOTE: モデルがスレッドセーフでない場合はロックを取得して推論を実行します
        with self._lock:
            detections = self.object_detector.detect(image)
        if not detections:
            return None
        # NOTE: ここで検出結果をもとに解析ロジックを実装できます。
        # デバイスコンテキストやユーザー設定、ディベロッパー設定でラベルごとの閾値を調整することも可能です。

        # 結果を整形して返却
        # NOTE: dataフィールドには任意のJSONシリアライズ可能なデータを設定できます
        # ラベルのリストを設定することで画面上でのフィルタリングが可能になります
        # スコアを設定することで画面上でのフィルタリングが可能になります
        data = {"detections": detections}
        labels: list[str] = list({d["label"] for d in detections})
        score: float = max(d["score"] for d in detections) if detections else 0.0
        thumbnail_bytes = thumbnail_data(image)
        # デバイスコンテキストに変更があれば更新
        # NOTE: デバイスコンテキストは通知間隔などデバイスごとに内部で管理する情報を保持する想定です
        # デバイスコンテキストを戻り値として返却し、AnalyzeResponse.UpdateContextで出力することで、セッションが切り替わっても情報を引き継げます
        device_context_dict = {"last_updated_at": ts.ToDatetime(tzinfo=datetime.timezone.utc).isoformat()}
        return FrameAnalyzerResult(
            timestamp=ts,
            thumbnail_data=thumbnail_bytes,
            data=data,
            labels=labels,
            score=score,
            device_context=device_context_dict,
        )


def thumbnail_data(image: Image.Image) -> bytes:
    """
    与えられた画像を640x640以内にアスペクト比を維持して縮小しJPEG画像のバイト列を返します。

    Args:
        image: 画像

    Returns:
        640x640以内に縮小されたJPEG画像のバイト列
    """
    thumb = image.copy()
    thumb.thumbnail((640, 640))
    data = io.BytesIO()
    thumb.save(data, format="JPEG")
    return data.getvalue()


class Analyzer(AnalyzerServiceServicer):
    def __init__(self) -> None:
        super().__init__()
        self._frame_analyzer: FrameAnalyzer = FrameAnalyzer()
        logging.info("Initialized Analyzer service.")

    def Analyze(self, request: AnalyzeRequest, context: grpc.ServicerContext):
        """gRPCで呼び出されるAnalyzeメソッドの実装"""
        dt = request.images[0].timestamp.ToDatetime(datetime.UTC)
        image = Image.open(io.BytesIO(request.images[0].data))

        # metadataからの情報取得
        metadata = dict(context.invocation_metadata())
        request_id = metadata.get("request_id", "unknown")
        device_id = metadata.get("device_id", "unknown")
        # ユーザーおよびディベロッパーが指定したパラメータ情報をリクエストボディから取得
        parameters = (
            json.dumps({k: MessageToDict(v) for k, v in request.parameter.items()})
            if request.parameter
            else None
        )
        device_context = metadata.get("context")  # デバイスコンテキスト情報を取得

        logging.debug(
            "Analyzing request_id=%s device_id=%s device_context=%s, parameters=%s, timestamp=%s",
            request_id,
            device_id,
            device_context,
            parameters,
            dt,
        )
        # 画像解析を実行
        r = self._frame_analyzer.analyze_image(
            image,
            request.images[0].timestamp,
            device_id=device_id,
            device_context=device_context,
            parameters=parameters,
        )
        if r is None:
            logging.info(
                "No detection for request_id=%s device_id=%s timestamp=%s",
                request_id,
                device_id,
                dt,
            )
            return AnalyzeResponse()  # No detection

        # Create a response
        _, user_config, developer_config = self._frame_analyzer.convert_params(parameters)
        # Metrics
        # NOTE: ここでは解析結果のdataフィールドに含まれるdetections情報をもとにラベルごとのカウントを集計しています
        detections = r.data.get("detections", []) if r else []
        counter = Counter([d["label"] for d in detections if "label" in d])
        record_metrics = AnalyzeResponse.RecordMetrics(
            timestamp=request.images[0].timestamp,
            units=["5minutes", "hourly", "daily"],
            metrics=counter,
            daily_boundary_timezone="Asia/Tokyo",
        )
        # Event
        # NOTE: ここでは解析結果をもとにイベント情報を生成しています
        record_event = AnalyzeResponse.RecordEvent(
            timestamp=request.images[0].timestamp,
            type="detect.sample",
            event_index=str(request.images[0].timestamp.ToMilliseconds()),
            labels=r.labels,
            score=r.score,
            data=ParseDict(
                r.data,
                Struct(),
            ),
            # geometry_config_ids=[x["geometry_config_id"] for x in user_config["geometries"]], # ジオメトリを使う場合に設定
            picture=EventPicture(
                content_type="image/jpeg",
                data=r.thumbnail_data,
            ),
        )
        # Object
        # NOTE: トラッキング情報の更新などあればここで実装します
        record_object = AnalyzeResponse.RecordObject(
            start_timestamp=request.images[0].timestamp,
            end_timestamp=request.images[0].timestamp,
            type="detect.object",
            object_index=str(request.images[0].timestamp.ToMilliseconds()),
            labels=r.labels,
            score=r.score,
            data=ParseDict(
                r.data,
                Struct(),
            ),
            # geometry_config_ids=[x["geometry_config_id"] for x in user_config["geometries"]], # ジオメトリを使う場合に設定
            picture=[
                ObjectPicture(
                    label="sample",
                    content_type="image/jpeg",
                    data=r.thumbnail_data,
                )
            ],
        )
        # Device status
        # NOTE: ここでは解析結果をもとにデバイスステータス情報を生成します。
        # 例としては、多くの人が検出された場合に「混雑」としてステータスを更新するなどが考えられます。
        status = DeviceStatus()
        status.label = "Area1"
        status.status = "crowded"
        status.score = r.score
        # status.geometry_config_ids.extend([x["geometry_config_id"] for x in user_config["geometries"]]) # ジオメトリを使う場合に設定
        record_device_status = AnalyzeResponse.RecordDeviceStatus(
            timestamp=request.images[0].timestamp,
            device_status=[status],
        )
        # Update context
        # NOTE: デバイスコンテキストを出力することで、セッションが切り替わっても情報を引き継げます
        update_context = AnalyzeResponse.UpdateContext(
            context=ParseDict(
                r.device_context,
                Struct(),
            ),
        )
        response = AnalyzeResponse(
            record_metrics=record_metrics,
            record_event=record_event,
            record_object=record_object,
            record_device_status=record_device_status,
            update_context=update_context,
        )

        logging.info(
            "Analyzed request_id=%s device_id=%s timestamp=%s",
            request_id,
            device_id,
            dt,
        )
        return response


def run(port: int):
    # NOTE: 一つのAnalyzerインスタンスで同時に処理可能なデバイス数を指定してください
    server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=10))
    add_AnalyzerServiceServicer_to_server(Analyzer(), server)

    listen_addr = f"[::]:{port}"
    server.add_insecure_port(listen_addr)
    logging.info("Starting server on %s", listen_addr)

    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--port",
        type=int,
        default=os.getenv("ANALYZER_PORT", _GRPC_PORT),
        help="gRPC (h2c) port to listen",
    )

    args = parser.parse_args()

    run(port=args.port)
