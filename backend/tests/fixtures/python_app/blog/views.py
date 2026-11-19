def article_list(request):
    return []


class ArticleDetail:
    @classmethod
    def as_view(cls):
        return cls
