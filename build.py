import base64
import copy
import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from email.headerregistry import Address
from pathlib import Path
from typing import Any, cast
import tomllib

import jinja2
import pydantic
import httpx


@dataclass(kw_only=True, slots=True, frozen=True)
class Target:
    url: str
    name: str
    tag: str


@dataclass(kw_only=True, slots=True, frozen=True)
class Config:
    cmd: str
    context: dict[str, str] = field(default_factory=dict[str, str])
    target: list[Target]


@dataclass(kw_only=True, slots=True, frozen=True)
class File:
    content: bytes
    executable: bool = False


def main():
    pyproject = load_pyproject()

    project = pyproject["tool"]["pack-binary"]["project"]

    config = pydantic.TypeAdapter(Config).validate_python(
        pyproject["tool"]["pack-binary"]
    )

    output = Path("dist")
    output.mkdir(exist_ok=True, parents=True)

    for target in config.target:
        name = project["name"]
        version = project["version"]

        bin_ext = ".exe" if target.name.endswith(".exe") else ""
        package_name_with_version = name.replace("-", "_") + "-" + version

        files: dict[str, File] = {}

        url = jinja2.Template(target.url).render(**config.context)
        print("downloading", url)

        resp = httpx.get(url, follow_redirects=True)
        with io.BytesIO(resp.read()) as f:
            with zipfile.ZipFile(f) as zf:
                external_attr = zf.getinfo(target.name).external_attr
                with zf.open(target.name, "r") as bin:
                    executable = bin.read()

        executable_path = Path(
            package_name_with_version + ".data",
            "scripts",
            config.cmd + bin_ext,
        ).as_posix()

        files[executable_path] = File(content=executable, executable=True)

        dist_info_path = Path(package_name_with_version + ".dist-info")

        files[dist_info_path.joinpath("WHEEL").as_posix()] = File(
            content="\n".join(
                [
                    "Wheel-Version: 1.0",
                    "Generator: pack-binary (0.0.1)",
                    "Root-Is-Purelib: false",
                    "Tag: {}".format(target.tag),
                    "",
                ]
            ).encode()
        )

        meta_file = "\n".join(generate_metadata(project)) + "\n"

        files[dist_info_path.joinpath("METADATA").as_posix()] = File(
            content=meta_file.encode()
        )

        records: list[tuple[str, str, str]] = []
        for path, file in files.items():
            records.append(
                (
                    path,
                    "sha256="
                    + base64.urlsafe_b64encode(hashlib.sha256(file.content).digest())
                    .rstrip(b"=")
                    .decode(),
                    str(len(file.content)),
                )
            )

        records.append((dist_info_path.joinpath("RECORD").as_posix(), "", ""))

        files[dist_info_path.joinpath("RECORD").as_posix()] = File(
            content="\n".join(",".join(record) for record in records).encode() + b"\n"
        )

        wheel_name = "{}-{}.whl".format(package_name_with_version, target.tag)
        print("writing", wheel_name)
        with zipfile.ZipFile(
            output.joinpath(wheel_name), "w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            for name, file in files.items():
                info = zipfile.ZipInfo(name)
                if file.executable and not target.name.endswith(".exe"):
                    info.external_attr = external_attr
                with zf.open(info, "w") as dest:
                    dest.write(file.content)


def load_pyproject():
    p = tomllib.loads(
        Path(__file__, "..", "pyproject.toml").resolve().read_text("utf8")
    )

    return p


def generate_metadata(project: dict[str, Any]):
    meta = copy.deepcopy(project)
    yield "Metadata-Version: 2.4"

    name = meta.pop("name")
    yield "Name: {}".format(name)

    version = meta.pop("version")
    yield "Version: {}".format(version)

    requires_version = meta.pop("requires-python")
    yield "Requires-Python: {}".format(requires_version)

    classifiers = meta.pop("classifiers", [])
    for classifier in classifiers:
        yield "Classifier: {}".format(classifier)

    summary = meta.pop("description", None)
    if summary:
        yield "Summary: {}".format(summary)

    license_field = meta.pop("license", None)
    if license_field:
        yield "License: {}".format(license_field)

    keywords = meta.pop("keywords", None)
    if keywords:
        yield "Keywords: {}".format(",".join(keywords))

    for label, key in [("Author", "authors"), ("Maintainer", "maintainers")]:
        people: list[dict[str, str]] = cast(list[dict[str, str]], meta.pop(key, []))
        for person in people:
            author_name = person.get("name", "")
            author_email = person.get("email")
            if author_email:
                yield "{}: {}".format(
                    label,
                    Address(display_name=author_name, addr_spec=author_email),
                )
            else:
                yield "{}: {}".format(label, author_name)

    urls = meta.pop("urls", {})
    for url_name, url in urls.items():
        yield "Project-URL: {}, {}".format(url_name, url)

    readme = meta.pop("readme", None)
    if readme:
        yield "Description-Content-Type: text/markdown; charset=UTF-8; variant=GFM"
        yield ""
        yield Path(readme).read_text("utf8")

    if meta:
        raise ValueError(
            "keys {} from pyproject.toml is not supported".format(list(meta.keys()))
        )


if __name__ == "__main__":
    main()
