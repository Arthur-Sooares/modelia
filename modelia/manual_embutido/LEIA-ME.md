# Texto do Manual do Blender embutido

`manual_blender_5.1_pt.json.gz` tem o texto (sem imagens nem menus) das páginas do
**Manual do Blender 5.1 em português** que um curso de modelagem usa: primeiros passos, interface,
viewport 3D, layout de cena, malhas, modificadores e glossário (362 páginas, ~1 MB de texto).

Vem no projeto para o modo Explicar funcionar sem baixar o manual inteiro (~350 MB): a tela
Instalação só precisa indexar este arquivo com o modelo de embeddings.

- **Fonte:** Blender Documentation Team, *Blender 5.1 Manual*, https://docs.blender.org/manual/pt/5.1/
- **Licença:** Creative Commons Attribution-ShareAlike 4.0 (CC BY-SA 4.0),
  https://creativecommons.org/licenses/by-sa/4.0/
- **Alterações:** só o texto do artigo de cada página foi extraído; cada trecho guarda o link da
  página original, que o Modelia cita como fonte nas respostas.

Para gerar de novo (outra versão do manual): `uv run python -m modelia.manual --embutir`
