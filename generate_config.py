import argparse
import copy
import sys
import re
from pathlib import Path

import httpx
import tomllib


ROOT = Path(__file__).resolve().parent
PYPROJECT = ROOT / "pyproject.toml"
TAG_PATTERN = re.compile(r"\d+\.\d+\.\d+(?:\.\d+)?")
USER_AGENT = "dprint-py-config-generator"
DEFAULT_TAG = "0.51.1"

# (asset name, target config)
ASSET_TARGETS = [
    (
        "dprint-x86_64-pc-windows-msvc.zip",
        {"platform": "win32", "arch": "amd64", "name": "dprint.exe"},
    ),
    (
        "dprint-x86_64-unknown-linux-gnu.zip",
        {"platform": "linux", "arch": "amd64", "name": "dprint", "manylinux": "2.17"},
    ),
    (
        "dprint-aarch64-unknown-linux-gnu.zip",
        {"platform": "linux", "arch": "arm64", "name": "dprint", "manylinux": "2.17"},
    ),
    (
        "dprint-aarch64-apple-darwin.zip",
        {"platform": "osx", "arch": "arm64", "name": "dprint", "macos_target_version": "11.0"},
    ),
    (
        "dprint-x86_64-apple-darwin.zip",
        {"platform": "osx", "arch": "amd64", "name": "dprint", "macos_target_version": "11.0"},
    ),
]


def load_pack_binary_config():
    data = tomllib.loads(PYPROJECT.read_text("utf8"))
    pack_binary = data["tool"]["pack-binary"]
    project_order = list(pack_binary["project"].keys())
    return pack_binary, project_order


def fetch_release_assets(tag: str) -> dict[str, str]:
    url = f"https://api.github.com/repos/dprint/dprint/releases/tags/{tag}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }

    try:
        resp = httpx.get(url, headers=headers, timeout=10.0)
    except httpx.RequestError as exc:
        raise SystemExit(f"network error while fetching release metadata for {tag}: {exc}") from exc
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError:
        print(
            f"GitHub API returned {resp.status_code}, falling back to HTML scraping for {tag}",
            file=sys.stderr,
        )
    else:
        assets = {
            asset["name"]: asset["browser_download_url"]
            for asset in resp.json().get("assets", [])
        }
        if assets:
            return assets
        print("GitHub API returned no assets, falling back to HTML scraping", file=sys.stderr)

    html_url = f"https://github.com/dprint/dprint/releases/expanded_assets/{tag}"
    try:
        html_resp = httpx.get(html_url, headers=headers, timeout=10.0)
    except httpx.RequestError as exc:
        raise SystemExit(f"failed to fetch release page {html_url!r}: {exc}") from exc
    html_resp.raise_for_status()

    pattern = re.compile(
        rf"""
        href="
        (?P<url>
            (?:https://github.com)?
            /dprint/dprint/releases/download/{tag}/
            (?P<name>[^"?#]+)
        )"
        """,
        re.VERBOSE,
    )

    assets: dict[str, str] = {}
    for match in pattern.finditer(html_resp.text):
        url = match.group("url")
        if url.startswith("/"):
            url = "https://github.com" + url

        assets[match.group("name")] = url

    if not assets:
        raise SystemExit(f"failed to find assets on release page {html_url}")

    return assets


def validate_tag(tag: str) -> str:
    match = TAG_PATTERN.fullmatch(tag)
    if not match:
        raise SystemExit(f"tag {tag!r} is not in the expected format 'x.y.z'")
    return match.group(0)


def build_config(tag: str):
    tag = validate_tag(tag)
    base_config, project_order = load_pack_binary_config()

    assets = fetch_release_assets(tag)

    targets = []
    missing = []

    for asset_name, target_config in ASSET_TARGETS:
        url = assets.get(asset_name)
        if not url:
            missing.append(asset_name)
            continue

        targets.append({"url": url, **target_config})

    if missing:
        raise SystemExit(f"missing release assets: {', '.join(missing)}")

    project = copy.deepcopy(base_config["project"])
    project["version"] = normalize_python_version(tag)

    return (
        {
            "cmd": base_config["cmd"],
            "context": {"version": tag},
            "project": project,
            "target": targets,
        },
        project_order,
    )


def _quote(value: str) -> str:
    return f"'{value}'"


def _format_inline_table(data: dict[str, str]) -> str:
    return "{ " + ", ".join(f"{key} = {_quote(value)}" for key, value in data.items()) + " }"


def _format_list(values: list) -> str:
    if not values:
        return "[]"

    first = values[0]
    if isinstance(first, dict):
        lines = ["["]
        for item in values:
            lines.append(f"  {_format_inline_table(item)},")
        lines.append("]")
        return "\n".join(lines)

    lines = ["["]
    for item in values:
        lines.append(f"  {_quote(item)},")
    lines.append("]")
    return "\n".join(lines)


def render_pack_binary(config: dict, project_order: list[str]) -> str:
    lines = [
        "[tool.pack-binary]",
        f"cmd = {_quote(config['cmd'])}",
        "",
        f"context = {{ version = {_quote(config['context']['version'])} }}",
        "",
        "[tool.pack-binary.project]",
    ]

    project = config["project"]
    for key in project_order:
        value = project[key]
        if key == "urls":
            continue

        if isinstance(value, list):
            lines.append(f"{key} = {_format_list(value)}")
        else:
            lines.append(f"{key} = {_quote(value)}")

    urls = project.get("urls", {})
    if urls:
        lines.append("")
        lines.append("[tool.pack-binary.project.urls]")
        for key, value in urls.items():
            lines.append(f"{key} = {_quote(value)}")

    for target in config["target"]:
        lines.append("")
        lines.append("[[tool.pack-binary.target]]")
        lines.append(f"url = {_quote(target['url'])}")
        lines.append(f"name = {_quote(target['name'])}")
        lines.append(f"platform = {_quote(target['platform'])}")
        lines.append(f"arch = {_quote(target['arch'])}")
        manylinux = target.get("manylinux")
        if manylinux:
            lines.append(f"manylinux = {_quote(manylinux)}")
        macos_target_version = target.get("macos_target_version")
        if macos_target_version:
            lines.append(f"macos_target_version = {_quote(macos_target_version)}")

    return "\n".join(lines) + "\n"


def normalize_python_version(tag: str) -> str:
    """
    Dprint release tags are plain semver (x.y.z), while the Python package
    keeps an extra revision segment for potential re-packs.
    """
    parts = tag.split(".")
    return tag if len(parts) == 4 else f"{tag}.0"


def write_pyproject(block: str):
    original = PYPROJECT.read_text("utf8")
    # Match the [tool.pack-binary] section until the next top-level section header.
    match = re.search(r"(?ms)^\[tool\.pack-binary\].*?(?=^\[|\Z)", original)
    if not match:
        raise SystemExit("failed to find [tool.pack-binary] in pyproject.toml")

    start, end = match.span()
    suffix = original[end:]
    # Preserve separation when other sections follow this block.
    extra_newline = "\n" if suffix and not block.endswith("\n\n") else ""

    updated = original[:start] + block + extra_newline + suffix
    PYPROJECT.write_text(updated, "utf8")


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Generate pack-binary config from a dprint release"
    )
    parser.add_argument(
        "--tag",
        default=DEFAULT_TAG,
        help=f"dprint release tag to use (default: {DEFAULT_TAG})",
    )
    parser.add_argument(
        "--write-pyproject",
        action="store_true",
        help="replace the [tool.pack-binary] block in pyproject.toml",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write generated config to a separate file instead of stdout",
    )

    args = parser.parse_args(argv)

    config, project_order = build_config(args.tag)
    rendered = render_pack_binary(config, project_order)

    if args.write_pyproject:
        write_pyproject(rendered)
        return

    if args.output:
        args.output.write_text(rendered, "utf8")
        return

    sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
