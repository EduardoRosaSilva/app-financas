from rest_framework.routers import DefaultRouter
from .views import MercadoViewSet, ListaViewSet, ItemViewSet, HistoricoPrecoViewSet

router = DefaultRouter()
router.register(r'mercados', MercadoViewSet)
router.register(r'listas', ListaViewSet)
router.register(r'itens', ItemViewSet)
router.register(r'historico', HistoricoPrecoViewSet)

urlpatterns = router.urls