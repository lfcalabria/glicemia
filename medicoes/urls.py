from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "medicoes"

urlpatterns = [
    path("", views.painel, name="painel"),
    path("login/", auth_views.LoginView.as_view(template_name="medicoes/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("grupo/novo/", views.criar_grupo, name="criar_grupo"),
    path("medicamentos/", views.medicamentos, name="medicamentos"),
    path("medicamentos/novo/", views.novo_medicamento, name="novo_medicamento"),
    path("medicamentos/<int:pk>/alternar/", views.toggle_medicamento, name="toggle_medicamento"),
    path("historico/", views.historico, name="historico"),
    path("admin-marcacoes/", views.admin_marcacoes, name="admin_marcacoes"),
    path("analise/", views.analise, name="analise"),
    path("analise/pdf/", views.analise_pdf, name="analise_pdf"),
]