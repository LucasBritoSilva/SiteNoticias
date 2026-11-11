"""Roda os testes JavaScript da página de edição (precisam do Node.js; sem ele, são ignorados)."""
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js não instalado")
def test_logica_da_pagina_de_edicao():
    r = subprocess.run(["node", str(RAIZ / "tests" / "editar.test.js")], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr


def test_pagina_de_edicao_orienta_sobre_o_token():
    html = (RAIZ / "docs" / "editar.html").read_text(encoding="utf-8")
    assert "Contents" in html and "leitura e escrita" in html
    assert "localStorage" in html and "Salvar" in html
