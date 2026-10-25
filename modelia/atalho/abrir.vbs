' Abre o Modelia com dois cliques, sem janela de terminal (tela "0 · Abrindo o Modelia" do Figma).
' O atalho da area de trabalho (criado na tela Instalacao) roda este arquivo com o wscript.exe:
'   1. liga o servidor escondido ("uv run main.py", sem abrir o navegador sozinho);
'   2. abre na hora a pagina "Abrindo o Modelia...", que vai para o chat quando o servidor responder.
' Se o Modelia ja estiver aberto, o servidor novo nao sobe (a porta esta ocupada) e a pagina usa o que ja existe.

Set shell = CreateObject("WScript.Shell")
Set arquivos = CreateObject("Scripting.FileSystemObject")
raiz = arquivos.GetParentFolderName(arquivos.GetParentFolderName(arquivos.GetParentFolderName(WScript.ScriptFullName)))

shell.CurrentDirectory = raiz
shell.Environment("PROCESS")("MODELIA_SEM_NAVEGADOR") = "1"
shell.Run "uv run main.py", 0, False
shell.Run """" & raiz & "\modelia\site\abrindo.html""", 1, False
