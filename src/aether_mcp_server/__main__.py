import argparse
import os
import sys

# 默认使用 Hugging Face 官方服务；网络环境需要镜像时可通过环境变量覆盖。
if "HF_ENDPOINT" not in os.environ:
    os.environ["HF_ENDPOINT"] = "https://huggingface.co"

# Windows 无 symlink 支持时静默
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from .auth import JavaDelegationVerifier, load_delegation_secret
from .server import create_server, mcp, run_http_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Aether MCP Server.")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("stdio")

    http_parser = subparsers.add_parser("http")
    http_parser.add_argument(
        "--auth",
        action="store_true",
        help="Enable Bearer token authentication for HTTP requests.",
    )
    http_parser.add_argument("--host", default="127.0.0.1")
    http_parser.add_argument("--port", default=8000, type=int)

    subparsers.add_parser(
        "download-models",
        help="预下载 Docling 模型到本地缓存，避免首次调用超时。",
    )

    parser.set_defaults(command="stdio", auth=False, host="127.0.0.1", port=8000)
    return parser


def cmd_download_models() -> None:
    """预下载核心 Docling 模型（布局+表格）。OCR 和 formula 模型在使用时自动下载。"""
    from docling.utils.model_downloader import download_models

    print("正在下载核心 Docling 模型（布局分析、表格识别）...")
    print("提示：OCR 模型在使用时自动下载，无需预下载。")
    sys.stdout.flush()
    download_models(
        progress=True,
        with_rapidocr=False,
        with_code_formula=False,
        with_picture_classifier=False,
    )
    print("核心模型下载完成，可以启动服务了。")


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "download-models":
        cmd_download_models()
        return
    if args.command == "http":
        verifier = None
        if args.auth:
            delegation_secret = load_delegation_secret()
            if delegation_secret is None:
                raise ValueError("HTTP mode requires AETHER_MCP_DELEGATION_SECRET.")
            verifier = JavaDelegationVerifier(delegation_secret)
        http_mcp = create_server(
            verifier,
            host=args.host,
            port=args.port,
        )
        http_mcp.settings.host = args.host
        http_mcp.settings.port = args.port
        import anyio
        anyio.run(run_http_server, http_mcp, verifier, args.host, args.port)
        return
    mcp.run()


if __name__ == "__main__":
    main()
