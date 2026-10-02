"""
Análise da curva glicêmica — portabilidade do script standalone para o Django.

Modos de análise:
  1) grupo     — um grupo específico escolhido pelo usuário
  2) resumo    — comparativo de médias de todos os grupos
  3) ultimos   — últimos N grupos tratados como um grupo só
  4) completo  — todos os grupos tratados como um grupo só
  5) tudo      — apenas no PDF: resumo + cada grupo individualmente + completo

Ordem de apresentação em cada análise: tabela diária (1 linha por dia, com
destaque das células fora da normalidade), gráficos e resumo.

Nos modos mesclados (últimos N e completo) cada linha da tabela diária indica
o grupo de origem, e a medicação é apresentada separada por grupo.

Medidas aleatórias são sempre desconsideradas: apenas os contextos fixos
(CONTEXTOS_FIXOS) entram na análise.

Todos os gráficos são gerados com matplotlib (backend Agg, sem janela).
"""
import base64
import datetime
import io

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D

from .forms import CONTEXTOS_FIXOS
from .models import Grupo, MedicaoGlicemia

plt.style.use("seaborn-v0_8-whitegrid")

LIMITE_HIPO = 70
LIMITE_HIPER = 180

CORES = {
    "jejum": "#2c3e50",
    "apos_cafe": "#e74c3c",
    "antes_almoco": "#3498db",
    "apos_almoco": "#2ecc71",
    "antes_jantar": "#9b59b6",
    "apos_jantar": "#f39c12",
}
CORES_GRUPOS = ["#3498db", "#e74c3c", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c"]

# Cores das células fora da normalidade (tela e PDF)
CORES_CELULA = {
    "alto": "#f5b7b1",    # vermelho — acima de LIMITE_HIPER
    "baixo": "#fcf3cf",   # amarelo  — abaixo de LIMITE_HIPO
    "normal": "#eafaf1",  # verde claro — faixa normal
    "vazio": "#f2f3f4",   # sem medição
}


def _rotulo(contexto):
    return MedicaoGlicemia.Contexto(contexto).label


def _classe(valor):
    """Classifica um valor para o destaque da tabela."""
    if valor is None:
        return "vazio"
    if valor > LIMITE_HIPER:
        return "alto"
    if valor < LIMITE_HIPO:
        return "baixo"
    return "normal"


def _fig_png(fig):
    """Renderiza a figura em PNG base64 (para embutir na página)."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=110)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _limites(ax):
    ax.axhline(y=LIMITE_HIPO, color="red", linestyle="--", alpha=0.3)
    ax.axhline(y=LIMITE_HIPER, color="orange", linestyle="--", alpha=0.3)


def _periodo_txt(g):
    com_dados = [l["data"] for l in g["linhas"] if l["media_dia"] is not None]
    if not com_dados:
        return "sem marcações"
    return f"{com_dados[0].strftime('%d/%m')}–{com_dados[-1].strftime('%d/%m')}"


def _ticks_datas(ax, datas):
    ax.set_xticks(datas)
    ax.set_xticklabels(
        [d.strftime("%d/%m") for d in datas], rotation=45, ha="right", fontsize=8
    )


# ============================================================
# 1. PREPARAÇÃO DOS DADOS
# ============================================================
def obter_dados(usuario):
    """Estrutura de análise de TODOS os grupos do usuário, em ordem cronológica."""
    med_por_data = {}
    for m in (
        MedicaoGlicemia.objects.filter(usuario=usuario, contexto__in=CONTEXTOS_FIXOS)
        .order_by("data")
    ):
        med_por_data.setdefault(m.data, {})[m.contexto] = m.valor_mgdl

    dados = []
    for numero, grupo in enumerate(
        Grupo.objects.filter(usuario=usuario).order_by("data_inicio"), start=1
    ):
        datas = [
            grupo.data_inicio + datetime.timedelta(days=i)
            for i in range(grupo.quantidade_dias)
        ]
        linhas = []
        for d in datas:
            dia = med_por_data.get(d, {})
            valores = [dia.get(c) for c in CONTEXTOS_FIXOS]
            presentes = [v for v in valores if v is not None]
            linhas.append({
                "data": d,
                "valores": valores,
                "media_dia": float(np.mean(presentes)) if presentes else None,
            })

        medicacao = {"manha": [], "tarde": [], "noite": []}
        for vinculo in grupo.medicamentos_vinculados.filter(
            usuario=usuario
        ).select_related("medicamento"):
            med = vinculo.medicamento
            texto = med.nome + (f" ({med.dosagem})" if med.dosagem else "")
            if med.periodo in medicacao:
                medicacao[med.periodo].append(texto)

        medias_periodo = []
        for i in range(len(CONTEXTOS_FIXOS)):
            vals = [l["valores"][i] for l in linhas if l["valores"][i] is not None]
            medias_periodo.append(float(np.mean(vals)) if vals else None)

        medias_dia = [l["media_dia"] for l in linhas]
        presentes_dia = [v for v in medias_dia if v is not None]
        total_marcacoes = sum(
            1 for l in linhas for v in l["valores"] if v is not None
        )

        dados.append({
            "numero": numero,
            "grupo": grupo,
            "rotulo": f"Grupo {numero}",
            "linhas": linhas,
            "medias_periodo": medias_periodo,
            "media_geral": float(np.mean(presentes_dia)) if presentes_dia else None,
            "total_marcacoes": total_marcacoes,
            "divisoes": [],  # fronteiras entre grupos (só nos modos mesclados)
            "medicacao": {k: ", ".join(v) if v else "—" for k, v in medicacao.items()},
        })
    return dados


def montar_virtual(subconjunto, rotulo):
    """Une grupos em um 'grupo virtual' (modos últimos N e completo).

    Cada linha recebe 'grupo_rotulo' para identificar o grupo de origem
    na tabela diária, e 'medicacao_por_grupo' preserva a medicação de
    cada grupo separadamente.
    """
    linhas = []
    divisoes = []
    medicacao_por_grupo = []
    for i, g in enumerate(subconjunto):
        if i > 0 and g["linhas"]:
            divisoes.append(g["linhas"][0]["data"])
        for l in g["linhas"]:
            linhas.append({**l, "grupo_rotulo": g["rotulo"]})
        medicacao_por_grupo.append({
            "rotulo": g["rotulo"],
            "medicacao": g["medicacao"],
        })

    medias_periodo = []
    for i in range(len(CONTEXTOS_FIXOS)):
        vals = [l["valores"][i] for l in linhas if l["valores"][i] is not None]
        medias_periodo.append(float(np.mean(vals)) if vals else None)

    presentes_dia = [l["media_dia"] for l in linhas if l["media_dia"] is not None]
    total = sum(1 for l in linhas for v in l["valores"] if v is not None)

    medicacao = {"manha": [], "tarde": [], "noite": []}
    for g in subconjunto:
        for periodo, textos in g["medicacao"].items():
            if textos != "—":
                for texto in textos.split(", "):
                    if texto and texto not in medicacao[periodo]:
                        medicacao[periodo].append(texto)

    return {
        "numero": rotulo,
        "grupo": None,
        "rotulo": rotulo,
        "linhas": linhas,
        "medias_periodo": medias_periodo,
        "media_geral": float(np.mean(presentes_dia)) if presentes_dia else None,
        "total_marcacoes": total,
        "divisoes": divisoes,
        "medicacao": {k: ", ".join(v) if v else "—" for k, v in medicacao.items()},
        "medicacao_por_grupo": medicacao_por_grupo,
    }


# ============================================================
# 2. TABELA DIÁRIA E RESUMO (dados para a tela)
# ============================================================
def tabela_dias(g):
    """1 linha por dia com o valor e a classe de destaque de cada célula.

    Classe: 'alto' (>180, vermelho), 'baixo' (<70, amarelo),
    'normal' (70–180, verde claro) e 'vazio' (sem medição).
    Em grupos virtuais (mesclados), 'grupo' identifica o grupo de origem.
    """
    linhas = []
    for l in g["linhas"]:
        linhas.append({
            "data": l["data"],
            "grupo": l.get("grupo_rotulo"),
            "celulas": [{"valor": v, "classe": _classe(v)} for v in l["valores"]],
            "media": l["media_dia"],
            "media_classe": _classe(l["media_dia"]),
        })
    return linhas


def linhas_resumo(g):
    """Médias por período para o bloco de resumo da tela."""
    return [
        {"rotulo": _rotulo(c), "media": m, "classe": _classe(m)}
        for c, m in zip(CONTEXTOS_FIXOS, g["medias_periodo"])
    ]


# ============================================================
# 3. GRÁFICOS DE UM "GRUPO" (real ou virtual)
# ============================================================
def _fig_sequencia(g):
    fig, ax = plt.subplots(figsize=(11.69, 6))
    xs, ys, cs = [], [], []
    ticks_pos, ticks_lbl = [], []
    pos = 0
    # Posição x de início de cada dia no contador de marcações.
    # Necessário para posicionar as divisões entre grupos de forma robusta:
    # dias sem marcações não geram tick nem ponto, então o índice do dia na
    # lista de linhas NÃO corresponde a um índice em ticks_pos.
    inicio_dia = {}
    for linha in g["linhas"]:
        inicio_dia[linha["data"]] = pos
        contagem = 0
        for i, val in enumerate(linha["valores"]):
            if val is not None:
                xs.append(pos)
                ys.append(val)
                cs.append(CORES[CONTEXTOS_FIXOS[i]])
                contagem += 1
                pos += 1
        if contagem:
            ticks_pos.append(pos - contagem / 2)
            ticks_lbl.append(linha["data"].strftime("%d/%m"))
    if xs:
        ax.plot(xs, ys, color="#7f8c8d", linewidth=1, alpha=0.4, zorder=1)
        ax.scatter(xs, ys, c=cs, s=30, zorder=2, edgecolors="white", linewidth=0.5)
    for d in g["divisoes"]:
        inicio = inicio_dia.get(d)
        if inicio is None or inicio == 0:
            continue  # divisão sem marcações anteriores — nada a separar
        ax.axvline(
            x=inicio - 0.5,
            color="#e74c3c", linestyle="-", alpha=0.4, linewidth=2,
        )
    _limites(ax)
    itens = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor=CORES[c],
               markersize=8, label=_rotulo(c))
        for c in CONTEXTOS_FIXOS
    ]
    itens += [
        Line2D([0], [0], color="red", linestyle="--", alpha=0.5,
               label=f"Hipoglicemia ({LIMITE_HIPO})"),
        Line2D([0], [0], color="orange", linestyle="--", alpha=0.5,
               label=f"Hiperglicemia ({LIMITE_HIPER})"),
    ]
    if g["divisoes"]:
        itens.append(Line2D([0], [0], color="#e74c3c", linestyle="-", alpha=0.5,
                            linewidth=2, label="Divisão entre grupos"))
    ax.legend(handles=itens, loc="upper right", fontsize=7, ncol=2, framealpha=0.9)
    ax.set_title(f"{g['rotulo']} — Marcações em Sequência ({_periodo_txt(g)})",
                 fontsize=13, fontweight="bold")
    ax.set_ylabel("Glicemia (mg/dL)", fontsize=11)
    ax.set_xlabel("Sequência de marcações", fontsize=10)
    ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.3)
    if ticks_pos:
        ax.set_xticks(ticks_pos)
        ax.set_xticklabels(ticks_lbl, rotation=45, ha="right", fontsize=7)
    return fig


def _fig_periodos(g):
    fig, ax = plt.subplots(figsize=(11.69, 6))
    datas = [l["data"] for l in g["linhas"]]
    for i, c in enumerate(CONTEXTOS_FIXOS):
        ys = [np.nan if l["valores"][i] is None else l["valores"][i]
              for l in g["linhas"]]
        if not all(np.isnan(v) for v in ys):
            ax.plot(datas, ys, marker="o", markersize=5, linewidth=2,
                    label=_rotulo(c), color=CORES[c], alpha=0.85)
    for d in g["divisoes"]:
        ax.axvline(x=d, color="#e74c3c", linestyle="-", alpha=0.3, linewidth=1.5)
    _limites(ax)
    ax.set_title(f"{g['rotulo']} — Todos os Períodos ({_periodo_txt(g)})",
                 fontsize=13, fontweight="bold")
    ax.set_xlabel("Data", fontsize=11)
    ax.set_ylabel("Glicemia (mg/dL)", fontsize=11)
    ax.legend(loc="upper right", fontsize=7, ncol=2, framealpha=0.9)
    _ticks_datas(ax, datas)
    ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.3)
    return fig


def _fig_medias_diarias(g):
    fig, ax = plt.subplots(figsize=(11.69, 5.5))
    datas = [l["data"] for l in g["linhas"]]
    medias = [np.nan if l["media_dia"] is None else l["media_dia"]
              for l in g["linhas"]]
    cor = "#1abc9c"
    ax.plot(datas, medias, marker="o", markersize=6, linewidth=2.5,
            color=cor, alpha=0.85, label="Média do dia (6 períodos)")
    ax.fill_between(datas, medias, alpha=0.1, color=cor)
    _limites(ax)
    if g["media_geral"] is not None:
        ax.axhline(y=g["media_geral"], color=cor, linestyle=":", alpha=0.5,
                   linewidth=1.5, label=f"Média geral: {g['media_geral']:.0f} mg/dL")
    for d, v in zip(datas, medias):
        if not np.isnan(v):
            ax.annotate(f"{v:.0f}", (d, v), textcoords="offset points",
                        xytext=(0, 10), ha="center", fontsize=8,
                        color=cor, fontweight="bold")
    for d in g["divisoes"]:
        ax.axvline(x=d, color="#e74c3c", linestyle="-", alpha=0.2, linewidth=1.5)
    ax.set_title(f"{g['rotulo']} — Médias Diárias ({_periodo_txt(g)})",
                 fontsize=13, fontweight="bold")
    ax.set_xlabel("Data", fontsize=11)
    ax.set_ylabel("Glicemia Média (mg/dL)", fontsize=11)
    ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
    _ticks_datas(ax, datas)
    ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.3)
    return fig


def _fig_barras_periodos(g):
    fig, ax = plt.subplots(figsize=(8.27, 6))
    rotulos = [_rotulo(c) for c in CONTEXTOS_FIXOS]
    medias = [np.nan if m is None else m for m in g["medias_periodo"]]
    barras = ax.bar(rotulos, medias, color=[CORES[c] for c in CONTEXTOS_FIXOS],
                    alpha=0.85, edgecolor="white", linewidth=1.5, width=0.6)
    _limites(ax)
    for barra, val in zip(barras, medias):
        if not np.isnan(val):
            ax.text(barra.get_x() + barra.get_width() / 2, barra.get_height() + 3,
                    f"{val:.0f}", ha="center", va="bottom",
                    fontsize=11, fontweight="bold")
    validos = [m for m in medias if not np.isnan(m)]
    if validos:
        media_geral = float(np.mean(validos))
        ax.axhline(y=media_geral, color="#1abc9c", linestyle=":", alpha=0.5,
                   linewidth=2, label=f"Média geral: {media_geral:.0f} mg/dL")
        ax.set_ylim(bottom=0, top=max(validos) * 1.15)
    else:
        ax.set_ylim(bottom=0)
    ax.set_title(f"{g['rotulo']} — Médias por Período ({_periodo_txt(g)})",
                 fontsize=13, fontweight="bold")
    ax.set_ylabel("Glicemia Média (mg/dL)", fontsize=11)
    ax.set_xlabel("Período do dia", fontsize=11)
    ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
    ax.tick_params(axis="x", rotation=15)
    ax.grid(True, alpha=0.3, axis="y")
    return fig


def graficos_do_grupo(g):
    """Os 4 gráficos de um grupo (real ou virtual), em PNG base64."""
    return {
        "sequencia": _fig_png(_fig_sequencia(g)),
        "periodos": _fig_png(_fig_periodos(g)),
        "medias": _fig_png(_fig_medias_diarias(g)),
        "barras": _fig_png(_fig_barras_periodos(g)),
    }


# ============================================================
# 4. GRÁFICOS DO MODO RESUMO (comparação entre grupos)
# ============================================================
def _fig_resumo_medias(dados):
    fig, ax = plt.subplots(figsize=(10, 5.5))
    rotulos = [g["rotulo"] for g in dados]
    medias = [np.nan if g["media_geral"] is None else g["media_geral"] for g in dados]
    cores = [CORES_GRUPOS[i % len(CORES_GRUPOS)] for i in range(len(dados))]
    barras = ax.bar(rotulos, medias, color=cores, alpha=0.85,
                    edgecolor="white", linewidth=1.2)
    _limites(ax)
    for barra, val in zip(barras, medias):
        if not np.isnan(val):
            ax.text(barra.get_x() + barra.get_width() / 2, barra.get_height() + 3,
                    f"{val:.0f}", ha="center", va="bottom",
                    fontsize=10, fontweight="bold")
    validos = [m for m in medias if not np.isnan(m)]
    if validos:
        ax.axhline(y=float(np.mean(validos)), color="#1abc9c", linestyle=":",
                   alpha=0.6, linewidth=2,
                   label=f"Média de todos os grupos: {np.mean(validos):.0f} mg/dL")
        ax.legend(loc="upper right", fontsize=9)
        ax.set_ylim(bottom=0, top=max(validos) * 1.15)
    else:
        ax.set_ylim(bottom=0)
    ax.set_title("Resumo — Média geral por grupo", fontsize=13, fontweight="bold")
    ax.set_ylabel("Glicemia Média (mg/dL)", fontsize=11)
    ax.grid(True, alpha=0.3, axis="y")
    return fig


def _fig_resumo_evolucao(dados):
    fig, ax = plt.subplots(figsize=(10, 5.5))
    xs = [g["numero"] for g in dados]
    ys = [np.nan if g["media_geral"] is None else g["media_geral"] for g in dados]
    cor = "#1abc9c"
    ax.plot(xs, ys, marker="o", markersize=9, linewidth=2.5, color=cor, alpha=0.85)
    ax.fill_between(xs, ys, alpha=0.1, color=cor)
    _limites(ax)
    for x, y in zip(xs, ys):
        if not np.isnan(y):
            ax.annotate(f"{y:.0f}", (x, y), textcoords="offset points",
                        xytext=(0, 12), ha="center", fontsize=9,
                        fontweight="bold", color=cor)
    media_geral = [y for y in ys if not np.isnan(y)]
    if media_geral:
        ax.axhline(y=float(np.mean(media_geral)), color="#7f8c8d",
                   linestyle=":", alpha=0.6, linewidth=1.5,
                   label=f"Média global: {np.mean(media_geral):.0f} mg/dL")
        ax.legend(loc="upper right", fontsize=9)
    ax.set_title("Resumo — Evolução da média geral entre grupos",
                 fontsize=13, fontweight="bold")
    ax.set_xlabel("Grupo", fontsize=11)
    ax.set_ylabel("Glicemia Média (mg/dL)", fontsize=11)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"G{n}" for n in xs])
    ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.3)
    return fig


def _fig_resumo_por_contexto(dados):
    """Um mini-gráfico por contexto: evolução da média daquele período
    ao longo dos grupos (grade 2 colunas x 3 linhas)."""
    fig, eixos = plt.subplots(2, 3, figsize=(14, 8))
    eixos = eixos.flatten()
    xs = [g["numero"] for g in dados]
    rotulos_grupo = [f"G{n}" for n in xs]

    for i, c in enumerate(CONTEXTOS_FIXOS):
        ax = eixos[i]
        cor = CORES[c]
        ys = [np.nan if g["medias_periodo"][i] is None else g["medias_periodo"][i]
              for g in dados]
        if not all(np.isnan(v) for v in ys):
            ax.plot(xs, ys, marker="o", markersize=6, linewidth=2,
                    color=cor, alpha=0.9)
            ax.fill_between(xs, ys, alpha=0.1, color=cor)
            # Valor de cada grupo anotado acima do ponto
            for x, y in zip(xs, ys):
                if not np.isnan(y):
                    ax.annotate(f"{y:.0f}", (x, y), textcoords="offset points",
                                xytext=(0, 8), ha="center", fontsize=7,
                                fontweight="bold", color=cor)
        _limites(ax)
        ax.set_title(_rotulo(c), fontsize=11, fontweight="bold", color=cor)
        ax.set_xticks(xs)
        ax.set_xticklabels(rotulos_grupo, fontsize=8)
        ax.tick_params(axis="y", labelsize=8)
        ax.set_ylim(bottom=0)
        ax.grid(True, alpha=0.3)

    fig.suptitle(
        f"Resumo — Evolução por contexto ({LIMITE_HIPO}–{LIMITE_HIPER} mg/dL)",
        fontsize=14, fontweight="bold",
    )
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    return fig


def graficos_do_resumo(dados):
    return {
        "medias": _fig_png(_fig_resumo_medias(dados)),
        "evolucao": _fig_png(_fig_resumo_evolucao(dados)),
        "por_contexto": _fig_png(_fig_resumo_por_contexto(dados)),
    }


# ============================================================
# 5. PDF
# ============================================================
def _pag_resumo_pdf(pdf, g, titulo):
    """Página de resumo textual do grupo (medicação + médias).

    Em grupos virtuais (mesclados), a medicação é listada por grupo de origem.
    """
    fig = plt.figure(figsize=(11.69, 8.27))
    fig.text(0.5, 0.94, titulo, ha="center", fontsize=16, fontweight="bold")
    fig.text(0.5, 0.90, _periodo_txt(g), ha="center", fontsize=12, color="#555")

    y = 0.82
    fig.text(0.08, y, "Medicação vinculada", fontsize=12, fontweight="bold")
    y -= 0.05
    if g.get("medicacao_por_grupo"):
        for bloco in g["medicacao_por_grupo"]:
            fig.text(0.12, y, bloco["rotulo"], fontsize=10, fontweight="bold")
            y -= 0.03
            for periodo, textos in bloco["medicacao"].items():
                fig.text(0.16, y, f"{periodo.capitalize()}: {textos}",
                         fontsize=10, wrap=True)
                y -= 0.03
    else:
        for periodo, textos in g["medicacao"].items():
            fig.text(0.12, y, f"{periodo.capitalize()}: {textos}",
                     fontsize=10, wrap=True)
            y -= 0.035

    y -= 0.02
    fig.text(0.08, y, "Médias por período", fontsize=12, fontweight="bold")
    y -= 0.05
    for c, m in zip(CONTEXTOS_FIXOS, g["medias_periodo"]):
        valor = f"{m:.0f} mg/dL" if m is not None else "sem dados"
        fig.text(0.12, y, f"{_rotulo(c)}: {valor}", fontsize=10)
        y -= 0.035

    y -= 0.02
    if g["media_geral"] is not None:
        fig.text(0.08, y, f"Média geral: {g['media_geral']:.1f} mg/dL",
                 fontsize=12, fontweight="bold", color="#1abc9c")
        y -= 0.04
    fig.text(0.08, y, f"Total de marcações registradas: {g['total_marcacoes']}",
             fontsize=10)

    fig.text(0.08, 0.04, f"Referências: hipo < {LIMITE_HIPO} mg/dL | "
                         f"hiper > {LIMITE_HIPER} mg/dL",
             fontsize=9, color="#777")
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _pag_tabela_dias(pdf, g):
    """Página(s) com a tabela diária — 1 linha por dia, células coloridas.

    Vermelho = acima de LIMITE_HIPER · Amarelo = abaixo de LIMITE_HIPO.
    Em grupos virtuais (mesclados), inclui a coluna Grupo.
    Quebra em várias páginas (26 linhas por página) para grupos longos.
    """
    mesclado = g["grupo"] is None
    if mesclado:
        cabecalho = ["Data", "Grupo"] + [_rotulo(c) for c in CONTEXTOS_FIXOS] + ["Média"]
    else:
        cabecalho = ["Data"] + [_rotulo(c) for c in CONTEXTOS_FIXOS] + ["Média"]
    linhas = g["linhas"]
    por_pagina = 26
    for inicio in range(0, len(linhas), por_pagina):
        fatia = linhas[inicio:inicio + por_pagina]
        fig = plt.figure(figsize=(11.69, 8.27))
        fig.text(0.5, 0.95, f"{g['rotulo']} — Tabela diária",
                 ha="center", fontsize=13, fontweight="bold")
        ax = fig.add_axes([0.04, 0.06, 0.92, 0.85])
        ax.axis("off")

        dados_tabela = [cabecalho]
        cores_celulas = []
        for l in fatia:
            if mesclado:
                linha_txt = [l["data"].strftime("%d/%m/%y"), l.get("grupo_rotulo", "")]
                linha_cores = [CORES_CELULA["normal"], CORES_CELULA["vazio"]]
            else:
                linha_txt = [l["data"].strftime("%d/%m/%y")]
                linha_cores = [CORES_CELULA["normal"]]
            for v in l["valores"]:
                linha_txt.append("—" if v is None else str(v))
                linha_cores.append(CORES_CELULA[_classe(v)])
            linha_txt.append(
                f"{l['media_dia']:.0f}" if l["media_dia"] is not None else "—"
            )
            linha_cores.append(CORES_CELULA[_classe(l["media_dia"])])
            dados_tabela.append(linha_txt)
            cores_celulas.append(linha_cores)

        tabela = ax.table(cellText=dados_tabela, loc="upper center",
                          cellLoc="center")
        tabela.auto_set_font_size(False)
        tabela.set_fontsize(8)
        tabela.scale(1, 1.5)
        for j in range(len(cabecalho)):
            tabela[0, j].set_facecolor("#1e3a5f")
            tabela[0, j].get_text().set_color("white")
            tabela[0, j].set_text_props(fontweight="bold")
        for i, linha_cores in enumerate(cores_celulas, start=1):
            for j, cor in enumerate(linha_cores):
                if not (mesclado and j == 1):  # colunas Data e Grupo ficam neutras
                    tabela[i, j].set_facecolor(cor)

        fig.text(0.08, 0.02,
                 f"Vermelho = acima de {LIMITE_HIPER} mg/dL · "
                 f"Amarelo = abaixo de {LIMITE_HIPO} mg/dL",
                 fontsize=9, color="#555")
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)


def _pag_tabela_resumo_pdf(pdf, resumo_ctx):
    """Página do modo resumo: tabela comparativa entre grupos."""
    fig = plt.figure(figsize=(11.69, 8.27))
    fig.text(0.5, 0.94, "Resumo — Comparação entre grupos",
             ha="center", fontsize=16, fontweight="bold")
    ax = fig.add_axes([0.05, 0.08, 0.9, 0.8])
    ax.axis("off")
    cabecalho = (["Grupo", "Período", "Marcações", "Média geral"]
                 + [_rotulo(c) for c in CONTEXTOS_FIXOS])
    linhas_tab = [cabecalho]
    for item in resumo_ctx["tabela"]:
        linhas_tab.append([
            item["rotulo"],
            item["periodo"],
            str(item["total_marcacoes"]),
            item["media_geral"],
        ] + item["medias_periodo"])
    tabela = ax.table(cellText=linhas_tab, loc="center", cellLoc="center")
    tabela.auto_set_font_size(False)
    tabela.set_fontsize(9)
    tabela.scale(1, 1.6)
    for j in range(len(cabecalho)):
        tabela[0, j].set_facecolor("#d5e3f0")
        tabela[0, j].set_text_props(fontweight="bold")
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _pag_capa(pdf, titulo, subtitulo, dados):
    """Capa do PDF do modo 'tudo', com a lista de grupos incluídos."""
    fig = plt.figure(figsize=(11.69, 8.27))
    fig.text(0.5, 0.75, titulo, ha="center", fontsize=22, fontweight="bold")
    fig.text(0.5, 0.69, subtitulo, ha="center", fontsize=13, color="#555")

    fig.text(0.5, 0.62, "Conteúdo do documento",
             ha="center", fontsize=13, fontweight="bold")
    y = 0.57
    itens = [
        "1. Resumo — comparação entre todos os grupos",
        "2. Análise de cada grupo individualmente",
        "3. Análise completa — todos os grupos como um só",
    ]
    for item in itens:
        fig.text(0.30, y, item, fontsize=11)
        y -= 0.04

    y -= 0.02
    fig.text(0.5, y, "Grupos incluídos", ha="center", fontsize=13,
             fontweight="bold")
    y -= 0.04
    coluna1, coluna2 = 0.18, 0.52
    for i, g in enumerate(dados):
        x = coluna1 if i % 2 == 0 else coluna2
        periodo = _periodo_txt(g)
        fig.text(x, y, f"{g['rotulo']} ({periodo}) — "
                       f"{g['total_marcacoes']} marcações",
                 fontsize=10)
        if i % 2 == 1:
            y -= 0.035
    fig.text(0.5, 0.05,
             f"Referências: hipo < {LIMITE_HIPO} mg/dL | "
             f"hiper > {LIMITE_HIPER} mg/dL",
             ha="center", fontsize=9, color="#777")
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _bloco_grupo_pdf(pdf, g):
    """Seção completa de um grupo: resumo + tabela diária + 4 gráficos."""
    _pag_resumo_pdf(pdf, g, f"Análise — {g['rotulo']}")
    _pag_tabela_dias(pdf, g)
    for fig in (_fig_sequencia(g), _fig_periodos(g),
                _fig_medias_diarias(g), _fig_barras_periodos(g)):
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)


def _bloco_resumo_pdf(pdf, dados, resumo_ctx):
    """Seção do resumo comparativo: tabela + 3 gráficos."""
    _pag_tabela_resumo_pdf(pdf, resumo_ctx)
    for fig in (_fig_resumo_medias(dados), _fig_resumo_evolucao(dados),
                _fig_resumo_por_contexto(dados)):
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)


def _contexto_resumo_interno(dados):
    """Versão interna do resumo comparativo (usada no modo 'tudo')."""
    tabela = []
    anterior = None
    for g in dados:
        variacao = None
        if anterior is not None and anterior["media_geral"] is not None \
                and g["media_geral"] is not None:
            variacao = g["media_geral"] - anterior["media_geral"]
        tabela.append({
            "rotulo": g["rotulo"],
            "periodo": _periodo_txt(g),
            "total_marcacoes": g["total_marcacoes"],
            "media_geral": f"{g['media_geral']:.1f}" if g["media_geral"] is not None else "—",
            "medias_periodo": [
                f"{m:.1f}" if m is not None else "—" for m in g["medias_periodo"]
            ],
            "variacao": variacao,
        })
        anterior = g
    return {"tabela": tabela}


def gerar_pdf(usuario, modo, g, dados, resumo_ctx=None):
    """Gera o PDF conforme o modo e devolve os bytes."""
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        if modo == "grupo":
            _pag_resumo_pdf(pdf, g, f"Análise — {g['rotulo']}")
            _pag_tabela_dias(pdf, g)
            for fig in (_fig_sequencia(g), _fig_periodos(g),
                        _fig_medias_diarias(g), _fig_barras_periodos(g)):
                pdf.savefig(fig, bbox_inches="tight")
                plt.close(fig)
        elif modo in ("ultimos", "completo"):
            _pag_resumo_pdf(pdf, g, f"Análise mesclada — {g['rotulo']}")
            _pag_tabela_dias(pdf, g)
            for fig in (_fig_sequencia(g), _fig_periodos(g),
                        _fig_medias_diarias(g), _fig_barras_periodos(g)):
                pdf.savefig(fig, bbox_inches="tight")
                plt.close(fig)
        elif modo == "resumo":
            _pag_tabela_resumo_pdf(pdf, resumo_ctx)
            for fig in (_fig_resumo_medias(dados), _fig_resumo_evolucao(dados),
                        _fig_resumo_por_contexto(dados)):
                pdf.savefig(fig, bbox_inches="tight")
                plt.close(fig)
        elif modo == "tudo":
            # Capa com índice do documento
            _pag_capa(pdf, "Controle de Glicemia",
                      "Relatório completo — todas as análises", dados)
            # 1) Resumo comparativo de todos os grupos
            if resumo_ctx is None:
                resumo_ctx = _contexto_resumo_interno(dados)
            _bloco_resumo_pdf(pdf, dados, resumo_ctx)
            # 2) Cada grupo individualmente (resumo + tabela + gráficos)
            for g_individual in dados:
                _bloco_grupo_pdf(pdf, g_individual)
            # 3) Análise completa — todos os grupos mesclados
            virtual = montar_virtual(dados, "Análise completa (todos os grupos)")
            _pag_resumo_pdf(pdf, virtual, "Análise completa (todos os grupos)")
            _pag_tabela_dias(pdf, virtual)
            for fig in (_fig_sequencia(virtual), _fig_periodos(virtual),
                        _fig_medias_diarias(virtual),
                        _fig_barras_periodos(virtual)):
                pdf.savefig(fig, bbox_inches="tight")
                plt.close(fig)
    return buf.getvalue()