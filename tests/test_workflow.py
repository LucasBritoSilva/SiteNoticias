"""Confere o essencial do workflow lendo o texto (sem PyYAML, que não é dependência do projeto).

A execução real depende do GitHub Actions e não é coberta aqui.
"""
from pathlib import Path

TEXTO = (Path(__file__).parent.parent / ".github" / "workflows" / "atualizar-site.yml").read_text(encoding="utf-8")


def test_horarios_em_utc():
    assert 'cron: "40 0 * * *"' in TEXTO   # 00:40 UTC = 20:40 em Cuiabá (UTC-4), dia anterior
    assert 'cron: "5 9 * * *"' in TEXTO    # 09:05 UTC = 05:05 em Cuiabá
    assert 'cron: "5 21 * * *"' in TEXTO   # 21:05 UTC = 17:05 em Cuiabá
    assert TEXTO.count("- cron:") == 3


def test_gatilho_manual_e_concorrencia():
    assert "workflow_dispatch:" in TEXTO
    assert "concurrency:" in TEXTO and "cancel-in-progress: false" in TEXTO


def test_segredo_vem_de_secrets_e_nao_esta_no_arquivo():
    assert "${{ secrets.GEMINI_API_KEY }}" in TEXTO
    assert "AIza" not in TEXTO


def test_push_so_depois_de_gerar_e_com_nova_tentativa():
    assert TEXTO.index("python scripts/main.py") < TEXTO.index("git push")
    assert "git pull --rebase" in TEXTO and "--force" not in TEXTO


def test_retentativa_unica_em_1_hora_so_para_gemini_indisponivel():
    assert "retry:" in TEXTO and "type: boolean" in TEXTO
    reagendar = TEXTO[TEXTO.index("  reagendar:"):]
    assert "needs.atualizar.outputs.codigo == '75'" in reagendar      # só 503/timeout do Gemini
    assert "github.event.inputs.retry != 'true'" in reagendar         # a retentativa não reagenda
    assert "failure()" in reagendar and "sleep 3600" in reagendar
    assert "-f retry=true" in reagendar and "actions: write" in reagendar
    assert 'echo "codigo=$codigo" >> "$GITHUB_OUTPUT"' in TEXTO and "exit $codigo" in TEXTO


def test_workflow_versiona_o_cache_dos_resumos():
    assert "git add -A dados" in TEXTO
