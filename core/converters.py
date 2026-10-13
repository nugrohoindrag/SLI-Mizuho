from typing import Any

from common.enum.choices import DocType, AktaType


class AktaTypeConv:
    regex = r"(reguler|oc|mu|pf)"
    def to_python(self, v: str) -> Any:
        return AktaType(v)
    def to_url(self, v: Any) -> str:
        return v.value if isinstance(v, AktaType) else str(v)

class DocTypeConv:
    regex = r"(akta|salinan)"
    def to_python(self, v: str) -> Any:
        return DocType(v)
    def to_url(self, v: Any) -> str:
        return v.value if isinstance(v, DocType) else str(v)