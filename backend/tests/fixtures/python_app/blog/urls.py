from django.urls import path, re_path

from . import views

urlpatterns = [
    path("articles/", views.article_list, name="list"),
    path("articles/<int:pk>/", views.ArticleDetail.as_view()),
    re_path(r"^old/(?P<y>\d+)/$", views.article_list),
]
