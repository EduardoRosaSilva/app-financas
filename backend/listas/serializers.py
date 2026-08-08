from rest_framework import serializers
from .models import Mercado, Lista, Item, HistoricoPreco


class MercadoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Mercado
        fields = '__all__'


class ItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = Item
        fields = '__all__'


class ListaSerializer(serializers.ModelSerializer):
    itens = ItemSerializer(many=True, read_only=True)
    mercado_nome = serializers.CharField(source='mercado.nome', read_only=True)

    class Meta:
        model = Lista
        fields = '__all__'


class HistoricoPrecoSerializer(serializers.ModelSerializer):
    mercado_nome = serializers.CharField(source='mercado.nome', read_only=True)

    class Meta:
        model = HistoricoPreco
        fields = '__all__'
        