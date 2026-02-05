静止画解析機（Analyzer）のリファレンス実装
----------------------------

## 解析処理の実装

AIソリューションプラットフォーム上で稼働するAnalyzerはgRPCサーバーとして動作します。
開発者は、AIソリューションプラットフォームで定義されているプロトコル定義ファイルに従ってAnalyzerのgRPCインターフェースを実装する必要があります。

本リファレンス実装は、静止画解析用のAnalyzerをPythonで実装したものです。`analyzer/main.py`に解析のメインの実装を記述しており、
開発者はこのメインコードを編集して開発することで、gRPCの実装について深い知識がなくとも、所望の解析を実装可能です。詳細は、`analyzer/main.py`のコード内のコメントを参照ください。

## 単体テストの実装

`tests/test_main.py`に、pytestを用いた単体テストの実装例を用意しています。実装したAnalyzerの仕様に合わせて`test_request`のfixtureを修正することで、Analyzerを実際にAIソリューションプラットフォーム上に登録する前に、手元の環境で最低限の動作確認が実施できます。

単体テストは以下のように実行します。
```sh
$ uv sync --group dev
$ uv run pytest tests/
```

## Analyzerのローカルでの起動

### 環境条件

- python 3.10.x 以上
- uv

デフォルトでは、python3.14が指定されています。バージョンを変更する場合は、`.python-version`ファイルを編集してください。

### 事前準備

必要なPythonパッケージをインストールします。
```sh
$ uv sync
```

protoファイルに更新がある場合は、以下を実行します。
```sh
$ uv run python -m grpc_tools.protoc \
    --proto_path=. \
    --python_out=. \
    --grpc_python_out=. \
    --mypy_out=. \
    proto/snapshot/v1/analyzer.proto
```

### Analyzerの起動
作成したAnalyzerをローカル環境で起動するには、以下のようにコマンドを実行します。
```sh
$ uv run python -m analyzer.main
```

### Analyzerの動作確認

`tools/snapshot_analyzer_client.py` に、Analyzerに対してgRPCリクエストを送信するためのクライアントツールを用意しています。引数に実際の画像データを渡すことで、解析処理の結果画像の確認やパラメータチューニングなどが実施できます。以下のように実行します。

クライアントツールを用いて、ローカル起動したAnalyzerに対してgRPCリクエストを発行します。
```sh
$ uv run python -m tools.snapshot_analyzer_client -i samples/test.jpg --device-id test-device-1
```

特定の`user_config`、`developer_config`が指定された場合の動作を確認する場合は、以下のようにJSONファイルを指定します。
```sh
$ uv run python -m tools.snapshot_analyzer_client -i samples/test.jpg --user-config samples/user_config.json --developer-config samples/developer_config.json --geometry-config samples/geometry_config.json
```

特定の`context`を指定して動作を確認する場合は、以下のように`--context`オプションを指定します。  
デプロイの更新などでセッションが再接続された時に引き継ぎたい情報があればデバイスコンテキストで出力すれば次のセッションでも情報が引き継がれます。  
この引き継がれる情報(最後の通知時間など)を`--context`に設定します。
```sh
$ uv run python -m tools.snapshot_analyzer_client -i samples/test.jpg --context samples/context.json
```

他のリクエストパラメータの指定方法は、以下のhelpを参照してください。
```sh
$ uv run python -m tools.snapshot_analyzer_client -h
```

## Dockerイメージファイルの作成

AIソリューションプラットフォーム上にAnalyzerを登録するためには、実装したAnalyzerをtar.gz形式のDockerイメージファイルを作成する必要があります。Dockerイメージファイルは以下のように作成します。

```sh
$ docker build -t sample-snapshot-analyzer .
$ docker save sample-snapshot-analyzer | gzip > sample-snapshot-analyzer.tar.gz
```

正常終了すると、`sample-snapshot-analyzer.tar.gz`という名前でDockerイメージファイルが生成されます。この生成されたtar.gz形式ファイルをAIソリューションプラットフォーム上に登録します。
