"""Confere o essencial do workflow lendo o texto (sem PyYAML, que não é dependência do projeto).

A execução real depende do GitHub Actions e não é coberta aqui.
"""
from pathlib import Path

TEXTO = (Path(__file__).parent.parent / ".github" / "workflows" / "atualizar-site.yml").read_text(encoding="utf-8")


def test_um_unico_agendamento_diario_as_22_17_utc():
    assert "- cron: '17 22 * * *'" in TEXTO    # 18:17 em Mato Grosso do Sul (UTC-4)
    assert TEXTO.count("- cron:") == 1


def test_horarios_do_rodape_batem_com_o_cron():
    import re
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    import site_builder

    crons = re.findall(r"cron: '(\d+) (\d+) \* \* \*'", TEXTO)
    brasilia = sorted(f"{(int(h) - 3) % 24:02d}:{int(m):02d}" for m, h in crons)
    assert brasilia == sorted(site_builder.HORARIOS_ATUALIZACAO)


def test_gatilho_manual_e_concorrencia():
    assert "workflow_dispatch:" in TEXTO
    assert "concurrency:" in TEXTO and "cancel-in-progress: false" in TEXTO


def test_segredo_vem_de_secrets_e_nao_esta_no_arquivo():
    assert "${{ secrets.GEMINI_API_KEY }}" in TEXTO
    assert "AIza" not in TEXTO


def test_push_so_depois_de_gerar_e_com_nova_tentativa():
    assert TEXTO.index("python -u scripts/main.py") < TEXTO.index("git push")
    assert "git pull --rebase" in TEXTO and "--force" not in TEXTO


def test_a_politica_de_tentativas_fica_em_um_unico_passo():
    # o comportamento do passo é testado de verdade em test_tentativas.py
    assert TEXTO.count("python -u scripts/main.py") == 1
    assert "ESPERA_SEGUNDOS=3600" in TEXTO and "tentar 2" in TEXTO and "tentar 3" not in TEXTO
    # nada de segundo job, redisparo do workflow ou entrada de retentativa
    for antigo in ("reagendar", "gh workflow run", "retry", "actions: write"):
        assert antigo not in TEXTO, antigo


def test_falha_definitiva_nao_e_escondida():
    assert "continue-on-error" not in TEXTO and "always()" not in TEXTO
    assert "if:" not in TEXTO                  # o envio usa o padrão: só roda se a geração deu certo


def test_timeout_do_job_comporta_a_espera_e_as_duas_tentativas():
    import re
    minutos = int(re.search(r"timeout-minutes: (\d+)", TEXTO).group(1))
    limite = int(re.search(r"LIMITE_POR_TENTATIVA=(\d+)m", TEXTO).group(1))
    assert minutos >= 60 + 2 * limite + 5


def test_execucao_na_fila_usa_a_ponta_atual_da_branch():
    assert "ref: ${{ github.ref_name }}" in TEXTO


def test_workflow_versiona_o_cache_dos_resumos():
    assert "git add -A dados" in TEXTO
