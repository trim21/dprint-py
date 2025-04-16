import base64
import copy
import hashlib
import io
import zipfile
from dataclasses import dataclass
from email.headerregistry import Address
from pathlib import Path
from typing import Any, Literal
import tomllib

import cattrs
import httpx

_linux_arch_map = {
    "arm64": "aarch64",
    "amd64": "x86_64",
}

_osx_arch_map = {
    "arm64": "arm64",
    "amd64": "x86_64",
}


@dataclass(kw_only=True, slots=True, frozen=True)
class Target:
    url: str
    name: str
    platform: Literal["win32", "linux", "osx"]
    arch: Literal["amd64", "arm64"]

    manylinux: str | None = None
    musllinux: str | None = None

    macos_target_version: str | None = None

    def wheel_tag(self) -> str:
        if self.platform == "win32":
            return "py3-none-win_{}".format(self.arch)
        if self.platform == "linux":
            if not self.manylinux:
                raise Exception(
                    "manylinux is required for target(url={!r})".format(self.url)
                )
            return "py3-none-{}_{}".format(self.manylinux, _linux_arch_map[self.arch])
        if self.platform == "osx":
            return "py3-none-macosx_{}_{}".format(
                self.macos_target_version.replace(".", "_"), _osx_arch_map[self.arch]
            )


@dataclass(kw_only=True, slots=True, frozen=True)
class Config:
    cmd: str
    target: list[Target]


def main():
    pyproject = load_pyproject()

    project = pyproject["project"]

    config = cattrs.structure(pyproject["tool"]["pack-binary"], Config)

    output = Path("dist")
    output.mkdir(exist_ok=True, parents=True)

    for target in config.target:
        name = project["name"]
        version = project["version"]

        bin_ext = ".exe" if target.platform == "win32" else ""
        package_name_with_version = name.replace("-", "_") + "-" + version

        files = {}

        resp = httpx.get(target.url, follow_redirects=True)
        with io.BytesIO(resp.read()) as f:
            with zipfile.ZipFile(f) as zf:
                with zf.open(target.name, "r") as bin:
                    executable = bin.read()

        executable_path = Path(
            package_name_with_version + ".data",
            "scripts",
            config.cmd + bin_ext,
        ).as_posix()

        files[executable_path] = executable

        dist_info_path = Path(package_name_with_version + ".dist-info")

        files[dist_info_path.joinpath("WHEEL").as_posix()] = "\n".join(
            [
                "Wheel-Version: 1.0",
                "Generator: pack-binary (0.0.1)",
                "Root-Is-Purelib: false",
                "Tag: {}".format(target.wheel_tag()),
                "",
            ]
        )

        meta_file = "\n".join(generate_metadata(project))

        files[dist_info_path.joinpath("METADATA").as_posix()] = meta_file

        records = [(dist_info_path.joinpath("RECORD").as_posix(), "", "")]
        for path, content in files.items():
            records.append(
                (
                    path,
                    base64.urlsafe_b64encode(
                        hashlib.sha256(ensure_binary(content)).digest()
                    )
                    .rstrip(b"=")
                    .decode(),
                    str(len(content)),
                )
            )

        files[dist_info_path.joinpath("RECORD").as_posix()] = "\n".join(
            ",".join(record) for record in records
        )

        with zipfile.ZipFile(
            output.joinpath(
                "{}-{}.whl".format(package_name_with_version, target.wheel_tag())
            ),
            "w",
        ) as zf:
            for file, content in files.items():
                zf.writestr(file, data=content)


def ensure_binary(s, encoding="utf-8", errors="strict"):
    if isinstance(s, bytes):
        return s
    if isinstance(s, str):
        return s.encode(encoding, errors)
    raise TypeError("not expecting type '%s'" % type(s))


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
