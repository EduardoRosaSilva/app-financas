from rest_framework import viewsets
from .models import Mercado, Lista, Item, HistoricoPreco
from .serializers import (
    MercadoSerializer, ListaSerializer, ItemSerializer, HistoricoPrecoSerializer
)


class MercadoViewSet(viewsets.ModelViewSet):
    queryset = Mercado.objects.all()
    serializer_class = MercadoSerializer


class ListaViewSet(viewsets.ModelViewSet):
    queryset = Lista.objects.all()
    serializer_class = ListaSerializer


class ItemViewSet(viewsets.ModelViewSet):
    queryset = Item.objects.all()
    serializer_class = ItemSerializer

    def perform_update(self, serializer):
        item = serializer.save()
        if item.preco and item.lista.mercado:
            HistoricoPreco.objects.create(
                item_nome=item.nome.strip().lower(),
                mercado=item.lista.mercado,
                preco=item.preco,
            )


class HistoricoPrecoViewSet(viewsets.ModelViewSet):
    queryset = HistoricoPreco.objects.all()
    serializer_class = HistoricoPrecoSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        item = self.request.query_params.get('item')
        if item:
            queryset = queryset.filter(item_nome=item.strip().lower())
        return queryset.order_by('-data')

