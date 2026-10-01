""".doc 转换链路测试（P1：soffice/antiword 均不可用时报明确引导文案）。

本环境（CI/沙箱）无 soffice/libreoffice/antiword，重点覆盖失败路径：
UnsupportedFormatError + 文案引导「转为 docx 后上传」。
"""
import pytest

from app.services.doc_extract import UnsupportedFormatError, extract_text


def _patch_no_converters(monkeypatch):
    """让 _extract_doc 认为环境里没有任何转换器。"""
    monkeypatch.setattr("shutil.which", lambda name: None)


def test_doc_without_converter_raises_guidance(tmp_path, monkeypatch):
    p = tmp_path / "legacy.doc"
    p.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 fake ole doc")
    _patch_no_converters(monkeypatch)
    with pytest.raises(UnsupportedFormatError) as ei:
        extract_text(p)
    msg = str(ei.value)
    assert "docx" in msg, "文案必须引导用户转为 docx"
    assert "上传" in msg


def test_doc_extractor_prefers_soffice_then_antiword(tmp_path, monkeypatch):
    """soffice 存在但转换失败 → 落到 antiword → 也失败 → 明确报错（顺序正确）。"""
    p = tmp_path / "old.doc"
    p.write_bytes(b"not really a doc")

    calls: list[str] = []

    def _fake_which(name):
        calls.append(name)
        if name in ("soffice", "antiword"):
            return f"/usr/bin/{name}"
        return None

    def _fake_run(cmd, **kwargs):
        class _R:
            returncode = 1
            stderr = b"boom"
        return _R()

    monkeypatch.setattr("shutil.which", _fake_which)
    monkeypatch.setattr("subprocess.run", _fake_run)
    with pytest.raises(UnsupportedFormatError):
        extract_text(p)
    assert "soffice" in calls and "antiword" in calls
