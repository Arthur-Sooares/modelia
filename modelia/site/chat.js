// Chat do Modelia. Conversa com a API em /api (modelia/api.py), que usa o mesmo agente do app.

const $ = (seletor) => document.querySelector(seletor);
const PLACEHOLDER = {
  Explicar: "Pergunte sobre o Blender…",
  Ensinar: "Peça uma tarefa, como “me ensina a fazer uma mesa”…",
  Demonstrar: "Peça um objeto…",
  Verificar: "Pergunte se ficou certo…",
};
const AVISO = {
  Explicar: "Explicar não mexe na sua cena.",
  Demonstrar: "Agora estude cada peça: selecione um objeto e pergunte no modo Explicar.",
  Verificar: "Verificar não altera nada na sua cena.",
};

let modo = "Ensinar";
let tarefas = [];
let ocupado = false;
let atual = null; // conversa aberta: {id, titulo, elemento, emEnsino}
const conversas = [];

// --- chamadas à API ---

async function api(caminho, corpo) {
  const resposta = await fetch(caminho, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corpo ?? {}),
  });
  const dados = await resposta.json().catch(() => ({}));
  if (!resposta.ok) throw new Error(dados.detail || `Erro ${resposta.status}`);
  return dados;
}

// Respostas do agente chegam aos pedaços (NDJSON, uma linha JSON por pedaço): {"parcial"}..., {"fim"} ou {"erro"}.
async function fluxo(caminho, corpo, aoParcial) {
  const resposta = await fetch(caminho, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corpo),
  });
  if (!resposta.ok) {
    const dados = await resposta.json().catch(() => ({}));
    throw new Error(dados.detail || `Erro ${resposta.status}`);
  }
  const leitor = resposta.body.pipeThrough(new TextDecoderStream()).getReader();
  let resto = "";
  for (;;) {
    const { value, done } = await leitor.read();
    if (done) throw new Error("A conexão com o servidor caiu antes do fim da resposta.");
    const linhas = (resto + value).split("\n");
    resto = linhas.pop();
    for (const linha of linhas.filter(Boolean)) {
      const item = JSON.parse(linha);
      if (item.erro) throw new Error(item.erro);
      if (item.fim) return item.fim;
      aoParcial(item.parcial);
    }
  }
}

async function situacao() {
  try {
    const s = await (await fetch("/api/situacao")).json();
    $("#estado-ollama").classList.toggle("desligado", !s.ollama);
    $("#estado-blender").classList.toggle("desligado", !s.blender);
    $("#ponto-cena").classList.toggle("desligado", !s.blender);
    const select = $("#modelo");
    const escolhido = select.value || s.modelo;
    select.replaceChildren(...s.modelos.map((m) => new Option(m, m, false, m === escolhido)));
    if (!s.modelos.length) select.append(new Option("nenhum modelo", ""));
    if (!tarefas.length) {
      tarefas = s.tarefas;
      $("#tarefa").replaceChildren(...tarefas.map((t) => new Option(`Tarefa: ${t.nome}`, t.id, false, t.id === "mesa")));
      mostrarTarefa();
    }
  } catch {
    $("#estado-ollama").classList.add("desligado");
  }
}

// --- texto das respostas: parágrafos, listas, **negrito** e `teclas` ---

function escapar(texto) {
  return texto.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

function emLinha(texto) {
  return escapar(texto)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, '<span class="tecla">$1</span>');
}

function blocos(texto) {
  // ```código``` vem antes dos parágrafos: pode ter linhas em branco dentro. Índices ímpares = código.
  return texto.split(/```[^\n]*\n([\s\S]*?)```/).flatMap((parte, i) => {
    if (i % 2 === 0) return paragrafos(parte);
    const pre = document.createElement("pre");
    pre.className = "bloco-codigo";
    pre.textContent = parte.replace(/\n$/, "");
    return [pre];
  });
}

function paragrafos(texto) {
  const saida = [];
  if (!texto.trim()) return saida;
  for (const bloco of texto.trim().split(/\n{2,}/)) {
    const linhas = bloco.split("\n").filter((l) => l.trim());
    const lista = linhas.length && linhas.every((l) => /^\s*([-*•]|\d+[.)])\s+/.test(l));
    if (lista) {
      const numerada = /^\s*\d/.test(linhas[0]);
      const el = document.createElement(numerada ? "ol" : "ul");
      for (const l of linhas) {
        const li = document.createElement("li");
        li.innerHTML = (numerada ? "" : '<img src="img/marcador-azul.svg" alt="">') +
          `<span>${emLinha(l.replace(/^\s*([-*•]|\d+[.)])\s+/, ""))}</span>`;
        el.append(li);
      }
      saida.push(el);
    } else {
      const p = document.createElement("p");
      p.innerHTML = linhas.map(emLinha).join("<br>");
      saida.push(p);
    }
  }
  return saida;
}

// --- mensagens ---

function rolar() {
  const centro = $("#centro");
  centro.scrollTo({ top: centro.scrollHeight, behavior: "smooth" });
}

function adicionarAluno(texto) {
  const el = $("#tpl-aluno").content.firstElementChild.cloneNode(true);
  el.querySelector("p").textContent = texto;
  atual.elemento.append(el);
  rolar();
}

function adicionarModelia() {
  const el = $("#tpl-modelia").content.firstElementChild.cloneNode(true);
  atual.elemento.append(el);
  rolar();
  return el.querySelector(".corpo");
}

function escrevendo(corpo, texto) {
  const aberto = (texto.match(/```/g) || []).length % 2; // bloco de código ainda sendo escrito
  corpo.replaceChildren(...blocos(aberto ? texto + "\n```" : texto));
  rolar();
}

function pensando(corpo) {
  corpo.innerHTML = '<p class="pensando"><img class="pisca" src="img/pulso.svg" alt="">Pensando…</p>';
}

function cartaoResultado({ tarefa, nota, certo, falta }) {
  const el = $("#tpl-resultado").content.firstElementChild.cloneNode(true);
  const [titulo, pilula] = el.querySelectorAll(".resultado-topo span");
  titulo.textContent = `Tarefa: ${tarefa}`;
  pilula.textContent = `Nota ${nota} de 3`;
  pilula.classList.toggle("cheia", nota === 3);
  const ul = el.querySelector("ul");
  for (const [itens, classe] of [[certo, ""], [falta, "falta"]]) {
    for (const item of itens) {
      const li = document.createElement("li");
      li.textContent = item;
      if (classe) li.className = classe;
      ul.append(li);
    }
  }
  return el;
}

function cartaoCodigo(codigo) {
  const el = $("#tpl-codigo").content.firstElementChild.cloneNode(true);
  el.querySelector("pre").textContent = codigo;
  const [executar, cancelar] = el.querySelectorAll("button");
  const decidir = async (acao, rotulo) => {
    if (ocupado) return;
    executar.disabled = cancelar.disabled = true;
    el.classList.add("feito");
    el.querySelector(".codigo-topo span").textContent = rotulo;
    await pedir((aoParcial) => fluxo(`/api/${acao}`, { conversa: atual.id }, aoParcial));
  };
  executar.addEventListener("click", () => decidir("executar", "Executado no Blender"));
  cancelar.addEventListener("click", () => decidir("cancelar", "Cancelado"));
  return el;
}

function mostrarResposta(corpo, r, modoDaResposta) {
  corpo.replaceChildren();
  let texto = r.texto;
  let fontes = "";
  const marca = texto.match(/\n*\*\*Fontes:\*\*\s*([\s\S]*)$/);
  if (marca) {
    fontes = marca[1];
    texto = texto.slice(0, marca.index);
  }
  texto = texto.replace(/\n*\*Veja a cena ao lado\.\*\s*$/, "");
  if (r.detalhes) corpo.append(cartaoResultado(r.detalhes));
  corpo.append(...blocos(texto));
  if (r.codigo) corpo.append(cartaoCodigo(r.codigo));
  if (fontes) {
    const p = document.createElement("p");
    p.className = "fontes";
    p.innerHTML = "<span>Fontes:</span>" + fontes.split(";").map((f) => `<span>${escapar(f.trim())}</span>`).join("");
    corpo.append(p);
  }
  if (AVISO[modoDaResposta]) {
    const p = document.createElement("p");
    p.className = "aviso";
    p.textContent = AVISO[modoDaResposta];
    corpo.append(p);
  }
}

// Envia algo à API e mostra a resposta numa mensagem nova do Modelia.
async function pedir(chamada) {
  ocupado = true;
  atualizarEnviar();
  const modoDoPedido = modo;
  const corpo = adicionarModelia();
  pensando(corpo);
  try {
    const r = await chamada((texto) => escrevendo(corpo, texto));
    mostrarResposta(corpo, r, modoDoPedido);
    atual.emEnsino = modoDoPedido === "Ensinar" && (Boolean(r.codigo) || atual.emEnsino);
    $("#texto").placeholder = atual.emEnsino ? "Escreva “próximo” ou “parar”…" : PLACEHOLDER[modo];
    if (r.atualizar_cena) atualizarCena();
  } catch (erro) {
    corpo.innerHTML = `<p class="erro">${escapar(erro.message)}</p>`;
  } finally {
    ocupado = false;
    atualizarEnviar();
    rolar();
  }
}

async function enviar(texto) {
  texto = texto.trim();
  if (!texto || ocupado) return;
  if (!atual) novaConversaLocal();
  if (!atual.id) {
    try {
      atual.id = (await api("/api/conversas")).conversa;
    } catch (erro) {
      alert(`Não consegui começar a conversa: ${erro.message}`);
      return;
    }
  }
  if (!atual.titulo) registrarNoHoje(texto);
  $("#boas-vindas").hidden = true;
  adicionarAluno(texto);
  await pedir((aoParcial) => fluxo("/api/mensagem", {
    conversa: atual.id, texto, modo, modelo: $("#modelo").value, tarefa: modo === "Verificar" ? $("#tarefa").value : "",
  }, aoParcial));
}

// --- conversas da sessão (lista "Hoje") ---

function novaConversaLocal() {
  atual?.elemento.remove();
  const elemento = document.createElement("div");
  elemento.className = "conversa";
  $("#centro").append(elemento);
  atual = { id: "", titulo: "", elemento, emEnsino: false };
  $("#boas-vindas").hidden = false;
  $("#texto").placeholder = "Pergunte ou peça uma tarefa…";
  marcarHoje();
}

function registrarNoHoje(texto) {
  atual.titulo = texto.length > 40 ? texto.slice(0, 38) + "…" : texto;
  conversas.unshift(atual);
  const conversa = atual;
  const botao = document.createElement("button");
  botao.type = "button";
  botao.className = "item";
  botao.textContent = conversa.titulo;
  botao.title = texto;
  botao.addEventListener("click", () => abrirConversa(conversa));
  conversa.botao = botao;
  $("#hoje .grupo-titulo").after(botao);
  $("#hoje").hidden = false;
  marcarHoje();
}

function abrirConversa(conversa) {
  if (ocupado || conversa === atual) return;
  atual.elemento.remove(); // uma conversa ainda vazia some; as outras continuam na lista
  atual = conversa;
  $("#centro").append(atual.elemento);
  $("#boas-vindas").hidden = true;
  marcarHoje();
  fecharMenu();
  rolar();
}

function marcarHoje() {
  for (const c of conversas) c.botao.setAttribute("aria-current", c === atual);
}

// --- modo, tarefa e painel ---

function escolherModo(novo) {
  modo = novo;
  document.body.dataset.modo = novo;
  for (const b of document.querySelectorAll(".lateral [data-modo]")) b.setAttribute("aria-current", b.dataset.modo === novo);
  for (const b of document.querySelectorAll(".modos button")) b.setAttribute("aria-pressed", b.dataset.modo === novo);
  $("#tarefa-rotulo").hidden = novo !== "Verificar";
  if (!atual?.emEnsino || novo !== "Ensinar") $("#texto").placeholder = atual?.titulo ? PLACEHOLDER[novo] : "Pergunte ou peça uma tarefa…";
  mostrarTarefa();
}

function mostrarTarefa() {
  const cartao = $("#painel-tarefa");
  const tarefa = tarefas.find((t) => t.id === $("#tarefa").value);
  cartao.hidden = modo !== "Verificar" || !tarefa;
  if (cartao.hidden) return;
  cartao.querySelector(".painel-cartao-titulo").textContent = `O que a tarefa “${tarefa.nome}” pede`;
  const li = document.createElement("li");
  li.textContent = tarefa.pedido;
  cartao.querySelector("ul").replaceChildren(li);
}

let urlCena = "";
async function atualizarCena() {
  if ($("#painel").hidden) return;
  const viewport = $("#viewport");
  try {
    const r = await fetch("/api/cena");
    if (r.status === 204) {
      viewport.innerHTML = "<p>O Blender não está conectado. Abra o Blender 5.1 e ligue o add-on MCP.</p>";
      return;
    }
    URL.revokeObjectURL(urlCena);
    urlCena = URL.createObjectURL(await r.blob());
    viewport.innerHTML = `<img src="${urlCena}" alt="Print da viewport 3D do Blender">`;
  } catch {
    viewport.innerHTML = "<p>Não consegui buscar a cena.</p>";
  }
}

function alternarPainel(abrir = $("#painel").hidden) {
  $("#painel").hidden = !abrir;
  $("#botao-cena").setAttribute("aria-expanded", abrir);
  if (abrir) atualizarCena();
}

function atualizarEnviar() {
  const temTexto = $("#texto").value.trim() !== "";
  $("#enviar").disabled = ocupado || !temTexto;
  $("#enviar").classList.toggle("pronto", temTexto && !ocupado);
}

function fecharMenu() {
  $("#lateral").classList.remove("aberta");
}

// --- ligações ---

for (const b of document.querySelectorAll(".lateral [data-modo], .modos button")) {
  b.addEventListener("click", () => {
    escolherModo(b.dataset.modo);
    fecharMenu();
    $("#texto").focus();
  });
}
for (const b of document.querySelectorAll(".sugestao")) {
  b.addEventListener("click", () => {
    escolherModo(b.dataset.modo);
    enviar(b.lastChild.textContent);
  });
}
$("#compositor").addEventListener("submit", (e) => {
  e.preventDefault();
  const texto = $("#texto").value;
  $("#texto").value = "";
  $("#texto").style.height = "";
  atualizarEnviar();
  enviar(texto);
});
$("#texto").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("#compositor").requestSubmit();
  }
});
$("#texto").addEventListener("input", (e) => {
  e.target.style.height = "";
  e.target.style.height = e.target.scrollHeight + "px";
  atualizarEnviar();
});
$("#nova").addEventListener("click", () => {
  if (ocupado) return;
  novaConversaLocal();
  fecharMenu();
  $("#texto").focus();
});
$("#tarefa").addEventListener("change", mostrarTarefa);
$("#botao-cena").addEventListener("click", () => alternarPainel());
$("#atualizar").addEventListener("click", atualizarCena);
$("#menu").addEventListener("click", () => $("#lateral").classList.toggle("aberta"));

novaConversaLocal();
escolherModo(modo);
atualizarEnviar();
situacao();
setInterval(situacao, 15000);
if (matchMedia("(min-width: 1181px)").matches) alternarPainel(true);
