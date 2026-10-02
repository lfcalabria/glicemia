import datetime
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from . import analise as an
from .forms import (
    CONTEXTOS_FIXOS,
    AdminMarcacoesForm,
    GrupoForm,
    MedicaoDiaForm,
    MedicamentoForm,
)
from .models import Grupo, GrupoMedicamento, MedicaoGlicemia, Medicamento


def _grupo_aberto(usuario):
    for grupo in Grupo.objects.filter(usuario=usuario):
        if grupo.esta_aberto():
            return grupo
    return None


@login_required
def painel(request):
    grupo = _grupo_aberto(request.user)
    if not grupo:
        return redirect("medicoes:criar_grupo")

    data = datetime.date.today()

    if request.method == "POST":
        form = MedicaoDiaForm(request.POST, grupo=grupo, dia=data)
        if form.is_valid():
            criadas = form.salvar()
            if criadas:
                messages.success(request, "Marca do dia registrada.")
            else:
                messages.info(request, "Nada novo para salvar — os campos já preenchidos são bloqueados.")
            return redirect("medicoes:painel")
    else:
        form = MedicaoDiaForm(grupo=grupo, dia=data)

    campos_dia = []
    for c in CONTEXTOS_FIXOS:
        campos_dia.append({
            "rotulo": MedicaoGlicemia.Contexto(c).label,
            "valor_field": form[c],
            "hora_field": form[f"{c}_hora"],
        })

    datas = [grupo.data_inicio + datetime.timedelta(days=i) for i in range(grupo.quantidade_dias)]
    meds = list(MedicaoGlicemia.objects.filter(usuario=request.user))
    tabela = []
    for d in datas:
        linha = {"data": d, "celulas": []}
        completo = True
        for c in CONTEXTOS_FIXOS:
            valor = next((m.valor_mgdl for m in meds if m.data == d and m.contexto == c), None)
            linha["celulas"].append((MedicaoGlicemia.Contexto(c).label, valor))
            if valor is None:
                completo = False
        linha["completo"] = completo
        tabela.append(linha)

    medicamentos_ativos = [
        gm.medicamento
        for gm in grupo.medicamentos_vinculados.select_related("medicamento").filter(usuario=request.user)
    ]

    context = {
        "grupo": grupo,
        "data": data,
        "form": form,
        "campos_dia": campos_dia,
        "tabela": tabela,
        "contextos_fixos": [(c, MedicaoGlicemia.Contexto(c).label) for c in CONTEXTOS_FIXOS],
        "medicamentos_ativos": medicamentos_ativos,
    }
    return render(request, "medicoes/painel.html", context)


@login_required
def criar_grupo(request):
    if request.method == "POST":
        form = GrupoForm(request.POST, usuario=request.user)
        if form.is_valid():
            grupo = form.save(commit=False)
            grupo.usuario = request.user
            grupo.save()
            for medicamento in Medicamento.objects.filter(usuario=request.user, ativo=True):
                GrupoMedicamento.objects.create(
                    usuario=request.user, grupo=grupo, medicamento=medicamento
                )
            messages.success(request, "Grupo criado e medicamentos ativos vinculados.")
            return redirect("medicoes:painel")
    else:
        form = GrupoForm(usuario=request.user)
    return render(request, "medicoes/criar_grupo.html", {"form": form})


@login_required
def medicamentos(request):
    """Lista de medicamentos com filtro por status: ativos, inativos ou todos."""
    filtro = request.GET.get("filtro") or "todos"
    queryset = Medicamento.objects.filter(usuario=request.user).order_by("nome")

    if filtro == "ativos":
        queryset = queryset.filter(ativo=True)
    elif filtro == "inativos":
        queryset = queryset.filter(ativo=False)
    else:
        filtro = "todos"

    return render(
        request,
        "medicoes/medicamentos.html",
        {"medicamentos": queryset, "filtro": filtro},
    )


@login_required
def novo_medicamento(request):
    if request.method == "POST":
        form = MedicamentoForm(request.POST)
        if form.is_valid():
            med = form.save(commit=False)
            med.usuario = request.user
            med.save()
            messages.success(request, "Medicamento cadastrado.")
            return redirect("medicoes:medicamentos")
    else:
        form = MedicamentoForm()
    return render(request, "medicoes/novo_medicamento.html", {"form": form})


@login_required
def toggle_medicamento(request, pk):
    med = get_object_or_404(Medicamento, pk=pk, usuario=request.user)
    med.ativo = not med.ativo
    med.save(update_fields=["ativo"])
    messages.success(request, f"Medicamento {med.nome} {'ativado' if med.ativo else 'inativado'}.")

    # Preserva o filtro atual na volta (se um filtro específico estiver ativo)
    filtro = request.GET.get("filtro")
    destino = reverse("medicoes:medicamentos")
    if filtro in ("ativos", "inativos"):
        destino += f"?filtro={filtro}"
    return redirect(destino)


@login_required
def historico(request):
    queryset = (
        MedicaoGlicemia.objects.filter(usuario=request.user)
        .order_by("data", "data_hora")
    )
    data_inicio = request.GET.get("data_inicio")
    data_fim = request.GET.get("data_fim")
    contexto = request.GET.get("contexto")

    if data_inicio:
        queryset = queryset.filter(data__gte=data_inicio)
    if data_fim:
        queryset = queryset.filter(data__lte=data_fim)
    if contexto:
        queryset = queryset.filter(contexto=contexto)

    paginador = Paginator(queryset, 50)  # 50 marcações por página
    pagina_num = request.GET.get("pagina") or 1
    pagina = paginador.get_page(pagina_num)

    # Preserva os filtros nos links de paginação
    filtros_qs = {
        "data_inicio": data_inicio or "",
        "data_fim": data_fim or "",
        "contexto": contexto or "",
    }
    querystring = urlencode({k: v for k, v in filtros_qs.items() if v})

    context = {
        "pagina": pagina,
        "medicoes": pagina.object_list,
        "contextos": MedicaoGlicemia.Contexto.choices,
        "filtros": filtros_qs,
        "querystring": querystring,
    }
    return render(request, "medicoes/historico.html", context)


@login_required
def admin_marcacoes(request):
    """Tela restrita a superadministradores: corrigir/incluir marcações de qualquer usuário.

    Comportamento dos seletores:
    - O usuário escolhido permanece selecionado (id vem da URL/POST e é
      comparado como string no template).
    - Se o usuário mudar, o grupo é descartado e a tela pede o período de novo.
    - Após salvar, o redirect preserva usuário e grupo (a tela continua aberta
      na mesma grade para conferência).
    """
    if not request.user.is_superuser:
        messages.error(request, "Acesso restrito a superadministradores.")
        return redirect("medicoes:painel")

    usuarios = User.objects.all().order_by("username")

    # Sempre string — o template compara com |stringformat:"s"
    usuario_id = request.GET.get("usuario") or request.POST.get("usuario") or ""
    grupo_id = request.GET.get("grupo") or request.POST.get("grupo") or ""

    grupos = []
    usuario = None
    grupo = None
    form = None
    tabela = []
    total_marcacoes = 0

    if usuario_id:
        usuario = get_object_or_404(User, pk=usuario_id)
        grupos = Grupo.objects.filter(usuario=usuario).order_by("-data_inicio")

        if grupo_id:
            grupo = get_object_or_404(Grupo, pk=grupo_id)
            # Troca de usuário com grupo antigo na URL: descarta o grupo e
            # pede a escolha do período novamente.
            if grupo.usuario_id != usuario.pk:
                grupo = None
                grupo_id = ""

    if grupo:
        # Fonte da verdade: o grupo escolhido define o usuário.
        usuario = grupo.usuario
        usuario_id = str(usuario.pk)

        if request.method == "POST":
            form = AdminMarcacoesForm(request.POST, grupo=grupo)
            if form.is_valid():
                criadas, atualizadas = form.salvar()
                messages.success(
                    request,
                    f"Salvo com sucesso: {criadas} marcação(ões) incluída(s) e {atualizadas} atualizada(s).",
                )
                # Preserva usuário e grupo na volta (em vez de limpar tudo).
                destino = urlencode({"usuario": usuario_id, "grupo": str(grupo.pk)})
                return redirect(f"{reverse('medicoes:admin_marcacoes')}?{destino}")
        else:
            form = AdminMarcacoesForm(grupo=grupo)

        total_marcacoes = MedicaoGlicemia.objects.filter(
            usuario=usuario, grupo=grupo
        ).count()

        meds = list(MedicaoGlicemia.objects.filter(usuario=usuario))

        datas = [
            grupo.data_inicio + datetime.timedelta(days=i)
            for i in range(grupo.quantidade_dias)
        ]
        for i, d in enumerate(datas):
            linha = {"data": d, "celulas": []}
            for c in CONTEXTOS_FIXOS:
                existente = next(
                    (m for m in meds if m.data == d and m.contexto == c),
                    None,
                )
                prefix = f"indice_{i}_{c}"
                linha["celulas"].append({
                    "rotulo": MedicaoGlicemia.Contexto(c).label,
                    "valor_field": form[f"{prefix}_valor"],
                    "hora_field": form[f"{prefix}_hora"],
                    "valor_atual": existente.valor_mgdl if existente else "",
                    "hora_atual": existente.data_hora.strftime("%H:%M") if existente else "",
                })
            tabela.append(linha)

    context = {
        "usuarios": usuarios,
        "usuario_id": str(usuario_id),
        "grupos": grupos,
        "grupo_id": str(grupo_id),
        "grupo": grupo,
        "form": form,
        "tabela": tabela,
        "contextos_fixos": [MedicaoGlicemia.Contexto(c).label for c in CONTEXTOS_FIXOS],
        "total_marcacoes": total_marcacoes,
    }
    return render(request, "medicoes/admin_marcacoes.html", context)


# ============================================================
# ANÁLISE DA CURVA GLICÊMICA
# ============================================================
def _parametros_analise(request, dados):
    """Lê modo/parâmetros da URL e devolve (modo, g, erro)."""
    modo = request.GET.get("modo") or "resumo"
    grupo_id = request.GET.get("grupo_id")
    n = request.GET.get("n") or "3"

    if modo == "tudo":
        # Existe apenas para o PDF; a tela só oferece o botão de gerar.
        return modo, None, None

    if modo == "grupo":
        if not grupo_id:
            return modo, None, "Escolha um grupo para analisar."
        if not any(str(g["grupo"].pk) == grupo_id for g in dados):
            return modo, None, "Grupo não encontrado."
        return modo, next(g for g in dados if str(g["grupo"].pk) == grupo_id), None

    if modo == "ultimos":
        try:
            n_int = max(1, int(n))
        except (TypeError, ValueError):
            return modo, None, "Número de grupos inválido."
        if not dados:
            return modo, None, "Nenhum grupo com dados para analisar."
        sub = dados[-n_int:]
        rotulo = f"Últimos {len(sub)} grupos (G{sub[0]['numero']}–G{sub[-1]['numero']})"
        return modo, an.montar_virtual(sub, rotulo), None

    if modo == "completo":
        if not dados:
            return modo, None, "Nenhum grupo com dados para analisar."
        return modo, an.montar_virtual(dados, "Análise completa (todos os grupos)"), None

    return "resumo", None, None


def _contexto_resumo(dados):
    """Monta a tabela comparativa do modo resumo."""
    tabela = []
    anterior = None
    for g in dados:
        variacao = None
        if anterior is not None and anterior["media_geral"] is not None \
                and g["media_geral"] is not None:
            variacao = g["media_geral"] - anterior["media_geral"]
        tabela.append({
            "rotulo": g["rotulo"],
            "periodo": an._periodo_txt(g),
            "total_marcacoes": g["total_marcacoes"],
            "media_geral": f"{g['media_geral']:.1f}" if g["media_geral"] is not None else "—",
            "medias_periodo": [
                f"{m:.1f}" if m is not None else "—" for m in g["medias_periodo"]
            ],
            "variacao": variacao,
        })
        anterior = g
    return {"tabela": tabela}


@login_required
def analise(request):
    """Página de análise com 4 modos + modo 'tudo' (que gera só o PDF).

    Ordem de apresentação: tabela diária (destaques de normalidade),
    depois os gráficos e por fim o resumo.
    """
    dados = an.obter_dados(request.user)

    modo, g, erro = _parametros_analise(request, dados)
    graficos = None
    resumo_ctx = None
    tabela_dias = None
    resumo_periodos = None

    if modo == "tudo":
        # A tela apenas informa o que o PDF incluirá.
        if not dados:
            erro = "Nenhum grupo com dados para gerar o relatório completo."
    elif erro is None and modo in ("grupo", "ultimos", "completo"):
        if g["total_marcacoes"] == 0:
            erro = "Este período não tem marcações registradas."
            g = None
        else:
            graficos = an.graficos_do_grupo(g)
            tabela_dias = an.tabela_dias(g)
            resumo_periodos = an.linhas_resumo(g)
    elif erro is None and modo == "resumo":
        if not dados:
            erro = "Nenhum grupo com dados para analisar."
        else:
            resumo_ctx = _contexto_resumo(dados)
            resumo_ctx["graficos"] = an.graficos_do_resumo(dados)

    context = {
        "modo": modo,
        "grupos": dados,
        "g": g,
        "graficos": graficos,
        "tabela_dias": tabela_dias,
        "resumo_periodos": resumo_periodos,
        "resumo": resumo_ctx,
        "erro": erro,
        "grupo_id_sel": request.GET.get("grupo_id") or "",
        "n_sel": request.GET.get("n") or "3",
        "querystring": request.GET.urlencode(),
        "contextos_fixos": [(c, MedicaoGlicemia.Contexto(c).label) for c in CONTEXTOS_FIXOS],
    }
    return render(request, "medicoes/analise.html", context)


@login_required
def analise_pdf(request):
    """Gera o PDF da análise no modo selecionado (incluindo 'tudo')."""
    dados = an.obter_dados(request.user)
    modo, g, erro = _parametros_analise(request, dados)

    if erro or (modo != "resumo" and modo != "tudo" and g is None):
        messages.error(request, erro or "Não foi possível gerar o PDF.")
        return redirect("medicoes:analise")

    if modo == "tudo":
        if not dados:
            messages.error(request, "Nenhum grupo com dados para gerar o relatório completo.")
            return redirect("medicoes:analise")
        vazio = all(gg["total_marcacoes"] == 0 for gg in dados)
        if vazio:
            messages.error(request, "Nenhum grupo tem marcações registradas.")
            return redirect("medicoes:analise")
        resumo_ctx = _contexto_resumo(dados)
        conteudo = an.gerar_pdf(request.user, "tudo", None, dados, resumo_ctx)
        nome_arquivo = "analise_glicemia_completa.pdf"
    elif modo == "resumo":
        if not dados:
            messages.error(request, "Nenhum grupo com dados para analisar.")
            return redirect("medicoes:analise")
        resumo_ctx = _contexto_resumo(dados)
        conteudo = an.gerar_pdf(request.user, modo, None, dados, resumo_ctx)
        nome_arquivo = "analise_glicemia.pdf"
    else:
        if g["total_marcacoes"] == 0:
            messages.error(request, "Este período não tem marcações registradas.")
            return redirect("medicoes:analise")
        conteudo = an.gerar_pdf(request.user, modo, g, dados)
        nome_arquivo = "analise_glicemia.pdf"

    resposta = HttpResponse(conteudo, content_type="application/pdf")
    resposta["Content-Disposition"] = f'attachment; filename="{nome_arquivo}"'
    return resposta