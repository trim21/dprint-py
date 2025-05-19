import base64
import copy
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from email.headerregistry import Address
from pathlib import Path
from typing import Any, Literal
import tomllib

import jinja2
import pydantic
import httpx

_linux_arch_map = {
    "arm64": "aarch64",
    "amd64": "x86_64",
}

_osx_arch_map = {
    "arm64": "arm64",
    "amd64": "x86_64",
}


version_pattern = re.compile(r"^\d+\.\d+$")


@dataclass(kw_only=True, slots=True, frozen=True)
class Target:
    url: str
    name: str
    platform: Literal["win32", "linux", "osx"]
    arch: Literal["amd64", "arm64"]

    manylinux: str | None = None

    macos_target_version: str | None = None

    def wheel_tag(self) -> str:
        if self.platform == "win32":
            return "py3-none-win_{}".format(self.arch)
        if self.platform == "linux":
            manylinux = self.manylinux
            if not manylinux:
                raise Exception(
                    "manylinux is required for target(url={!r})".format(self.url)
                )

            if not version_pattern.match(manylinux):
                raise ValueError(
                    "manylinux must match the pattern of {!r}, got {} instead".format(
                        version_pattern.pattern, manylinux
                    )
                )

            return "py3-none-manylinux_{}_{}".format(
                manylinux.replace(".", "_"), _linux_arch_map[self.arch]
            )

        if self.platform == "osx":
            macos_target_version = self.macos_target_version
            if not macos_target_version:
                raise Exception(
                    "manylinux is required for target(url={!r})".format(self.url)
                )

            if not version_pattern.match(macos_target_version):
                raise ValueError(
                    "macos_target_version must match the pattern of {!r}, got {} instead".format(
                        version_pattern.pattern, macos_target_version
                    )
                )

            return "py3-none-macosx_{}_{}".format(
                macos_target_version.replace(".", "_"), _osx_arch_map[self.arch]
            )

        raise ValueError("unexpected platform {}".format(self.platform))


@dataclass(kw_only=True, slots=True, frozen=True)
class Config:
    cmd: str
    context: dict[str, str] = field(default_factory=dict)
    target: list[Target]


@dataclass(kw_only=True, slots=True, frozen=True)
class File:
    content: bytes
    executable: bool = False


def main():
    pyproject = load_pyproject()

    project = pyproject["project"]

    config = pydantic.TypeAdapter(Config).validate_python(
        pyproject["tool"]["pack-binary"]
    )

    output = Path("dist")
    output.mkdir(exist_ok=True, parents=True)

    for target in config.target:
        name = project["name"]
        version = project["version"]
        wheel_tag = target.wheel_tag()

        bin_ext = ".exe" if target.platform == "win32" else ""
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
                    "Tag: {}".format(wheel_tag),
                    "",
                ]
            ).encode()
        )

        meta_file = "\n".join(generate_metadata(project)) + "\n"

        files[dist_info_path.joinpath("METADATA").as_posix()] = File(
            content=meta_file.encode()
        )

        records = []
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

        wheel_name = "{}-{}.whl".format(package_name_with_version, wheel_tag)
        print("writing", wheel_name)
        with zipfile.ZipFile(
            output.joinpath(wheel_name), "w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            for name, file in files.items():
                info = zipfile.ZipInfo(name)
                if file.executable and target.platform != "win32":
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

    license = meta.pop("license", None)
    if license:
        yield "License: {}".format(license)

    keywords = meta.pop("keywords", None)
    if keywords:
        yield "Keywords: {}".format(",".join(keywords))

    for people, meta_name in [
        [meta.pop("authors", []), "Author"],
        [meta.pop("maintainers", []), "Maintainer"],
    ]:
        for person in people:
            person_name = person.get("name")
            person_email = person.get("email")

            if person_email:

                yield "{}: {}".format(
                    meta_name, Address(display_name=person_name, addr_spec=person_email)
                )
            else:
                yield "{}: {}".format(meta_name, person_name)

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
