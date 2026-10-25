// Chat do Modelia. Conversa com a API em /api (modelia/api.py), que usa o mesmo agente do app.
// Telas do Figma: "3 · Chat (novo)" a "6 · Chat (Verificar)" e "Melhorias para o aluno (UX)" (13 a 14 e 0).

const $ = (seletor) => document.querySelector(seletor);
const MODOS = ["Explicar", "Ensinar", "Demonstrar", "Verificar"];
// O aluno vê os modos como frases; os nomes técnicos ficam no código, no banco e nas telas do professor.
const NOME = { Explicar: "Tenho uma dúvida", Ensinar: "Me ensina passo a passo", Demonstrar: "Faz pra mim",
  Verificar: "Confere meu trabalho" };
const PLACEHOLDER = "Escreva aqui o que você quer…";
const NO_ENSINO = "Escreva “próximo” ou “parar”…";
const AVISO = {
  Explicar: "Isso não mexe na sua cena.",
  Demonstrar: `Agora estude cada peça: selecione um objeto e pergunte em “${NOME.Explicar}”.`,
  Verificar: "Conferir não altera nada na sua cena.",
};

const pedidoNoEndereco = decodeURIComponent(location.hash.slice(1));
let modo = MODOS.includes(pedidoNoEndereco) ? pedidoNoEndereco : "Ensinar";
// Enquanto o aluno não escolhe o modo, um pedido de objeto no modo padrão pergunta antes (tela 13c).
let modoEscolhido = MODOS.includes(pedidoNoEndereco);
let modelo = "";        // o modelo padrão que o professor escolheu (o aluno não escolhe)
let tempoTipico = {};   // segundos típicos de cada modo neste computador, para a estimativa de espera
let tarefas = [];
let atual = null; // conversa aberta: {id, titulo, elemento, emEnsino, ocupado, botao}
const conversas = [];
const DECISAO = { executado: "Executado no Blender", cancelado: "Cancelado", nao_executado: "Não executado" };

// A conversa aberta fica guardada na aba: ao voltar de outra tela, o chat reabre nela.
function guardarAberta(id) {
  try { sessionStorage.setItem("modelia-conversa", id); } catch { /* aba anônima: abre uma nova */ }
}
function lerAberta() {
  try { return sessionStorage.getItem("modelia-conversa") || ""; } catch { return ""; }
}

// --- chamadas à API ---

const SEM_CONEXAO = "Perdi a conexão com o Modelia. Confira se o terminal com “uv run main.py” ainda está aberto " +
  "e tente de novo. Não precisa de internet: o Modelia roda neste computador.";

// O navegador avisa queda de conexão com um TypeError em inglês ("network error", "Failed to fetch"), mas um
// erro na própria página também é TypeError: antes de culpar a conexão, confere se o servidor responde.
async function textoDoErro(erro) {
  if (!(erro instanceof TypeError)) return erro.message;
  const vivo = await fetch("/api/situacao").then((r) => r.ok, () => false);
  return vivo ? `Algo deu errado nesta página (${erro.message}). O Modelia continua ligado: recarregue e tente de novo.`
    : SEM_CONEXAO;
}

async function api(caminho, corpo) {
  const resposta = await fetch(caminho, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corpo ?? {}),
  });
  const dados = await resposta.json().catch(() => ({}));
  if (!resposta.ok) throw new Error(dados.detail || `Erro ${resposta.status}`);
  return dados;
}

// Respostas do agente chegam aos pedaços (NDJSON, uma linha JSON por pedaço):
// {"parcial"} e {"etapa"}..., e por último {"fim"} ou {"erro"}. `avisos` = {parcial(texto), etapa(texto)}.
// Sem `corpo`, é um GET: retoma uma resposta que já estava sendo gerada (devolve null se ela já acabou).
async function fluxo(caminho, corpo, avisos) {
  const resposta = await fetch(caminho, corpo === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corpo),
  });
  if (resposta.status === 204) return null;
  if (!resposta.ok) {
    const dados = await resposta.json().catch(() => ({}));
    throw new Error(dados.detail || `Erro ${resposta.status}`);
  }
  const leitor = resposta.body.pipeThrough(new TextDecoderStream()).getReader();
  let resto = "";
  for (;;) {
    const { value, done } = await leitor.read();
    if (done) throw new Error(SEM_CONEXAO);
    const linhas = (resto + value).split("\n");
    resto = linhas.pop();
    for (const linha of linhas.filter(Boolean)) {
      const item = JSON.parse(linha);
      if (item.erro) throw new Error(item.erro);
      if (item.fim) return item.fim;
      if (item.etapa) avisos.etapa(item.etapa);
      else avisos.parcial(item.parcial);
    }
  }
}

// --- computador pronto? (telas "0 · Abrindo o Modelia" e "14 · Quase lá") ---

function mostrarTelaCheia(id) {
  for (const tela of document.querySelectorAll(".tela-cheia")) tela.hidden = tela.id !== id;
  for (const el of [$("#lateral"), $(".principal"), $("#painel")]) el.inert = Boolean(id);
}

// Devolve se o chat pode ser usado (sem conexão também devolve true: o erro aparece ao enviar).
async function situacao() {
  let s;
  try {
    s = await (await fetch("/api/situacao")).json();
  } catch {
    return true;
  }
  const pronto = s.ollama && s.modelos.length > 0 && !s.conhecimento_vazio;
  // Logo depois de abrir, o Modelia ainda está ligando o Ollama: isso é "abrindo", não "falta preparar".
  mostrarTelaCheia(pronto ? null : s.preparando ? "abrindo" : "quase-la");
  $("#ponto-cena").classList.toggle("desligado", !s.blender);
  $("#cena-rotulo").innerHTML = s.blender ? 'Cena<span class="cena-extra"> do Blender</span>' : "Blender fechado";
  modelo = s.modelo;
  tempoTipico = s.tempo_tipico || {};
  if (!tarefas.length) {
    tarefas = s.tarefas;
    $("#tarefa").replaceChildren(...tarefas.map((t) => new Option(`Tarefa: ${t.nome}`, t.id, false, t.id === "mesa")));
    mostrarTarefa();
  }
  return pronto;
}

async function vigiarSituacao() {
  const pronto = await situacao();
  setTimeout(vigiarSituacao, pronto ? 15000 : 2000); // esperando o professor preparar: confere mais vezes
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

// "faz uma cadeira" → sua cadeira / a cadeira / ela inteira. Sem "um/uma" no pedido, fala de "objeto".
function objetoDoPedido(pedido) {
  const achado = pedido.toLowerCase().match(/\b(um|uma)\s+([a-zà-ü]+)/);
  if (!achado) return { seu: "o seu objeto", o: "o objeto", inteiro: "ele inteiro" };
  const [, artigo, nome] = achado;
  return artigo === "uma" ? { seu: `sua ${nome}`, o: `a ${nome}`, inteiro: "ela inteira" }
    : { seu: `seu ${nome}`, o: `o ${nome}`, inteiro: "ele inteiro" };
}

// --- mensagens ---

function rolar() {
  const centro = $("#centro");
  centro.scrollTo({ top: centro.scrollHeight, behavior: "smooth" });
}

function adicionarAluno(texto, conversa = atual) {
  const el = $("#tpl-aluno").content.firstElementChild.cloneNode(true);
  el.querySelector("p").textContent = texto;
  conversa.elemento.append(el);
  rolar();
}

function adicionarModelia(conversa = atual) {
  const el = $("#tpl-modelia").content.firstElementChild.cloneNode(true);
  conversa.elemento.append(el);
  rolar();
  return el.querySelector(".corpo");
}

function escrevendo(corpo, texto) {
  const aberto = (texto.match(/```/g) || []).length % 2; // bloco de código ainda sendo escrito
  const etapa = corpo.querySelector(".pensando.etapa"); // continua visível acima do texto
  corpo.replaceChildren(...(etapa ? [etapa] : []), ...blocos(aberto ? texto + "\n```" : texto));
  rolar();
}

function pensando(corpo, texto = "Pensando…") {
  corpo.innerHTML = '<p class="pensando"><img class="pisca" src="img/pulso.svg" alt=""><span></span></p>';
  corpo.querySelector(".pensando span").textContent = texto;
}

// Etapa nova do agente: troca a linha de espera e apaga o texto da etapa anterior.
function naEtapa(corpo, texto) {
  pensando(corpo, texto);
  corpo.firstElementChild.classList.add("etapa");
  rolar();
}

function aproximado(segundos) {
  if (segundos < 50) return "menos de 1 minuto";
  const minutos = Math.round(segundos / 60);
  return minutos <= 1 ? "cerca de 1 minuto" : `uns ${minutos} minutos`;
}

// Cartão de espera do "Faz pra mim" (tela 13b): as etapas do agente viram uma lista com a atual em destaque.
function cartaoEspera(corpo, pedido) {
  const el = $("#tpl-espera").content.firstElementChild.cloneNode(true);
  const objeto = objetoDoPedido(pedido);
  el.querySelector("strong").textContent = `Montando ${objeto.seu}…`;
  const nomes = ["Entendi o pedido", "Escrevendo as instruções para o Blender", `Montando ${objeto.o} no Blender`,
    "Conferindo se ficou certo"];
  const lista = el.querySelector(".etapas");
  lista.replaceChildren(...nomes.map((nome) => Object.assign(document.createElement("li"), { textContent: nome })));
  const tipico = tempoTipico.Demonstrar;
  el.querySelector(".espera-nota").textContent = (tipico ? `Neste computador isso costuma levar ${aproximado(tipico)}. ` : "")
    + "Pode usar outras telas: eu continuo trabalhando e o resultado fica aqui.";
  corpo.replaceChildren(el);
  // Etapas do agente (agente.py): "Planejando as peças…", "Corrigindo o objeto (tentativa 2 de 3)…",
  // "Criando no Blender…" e "Conferindo a cena…". Antes da primeira, o pedido acabou de chegar.
  const etapa = (texto = "") => {
    const agora = !texto ? 0 : /^Criando/.test(texto) ? 2 : /^Conferindo/.test(texto) ? 3 : 1;
    [...lista.children].forEach((li, i) => {
      li.className = i < agora ? "feita" : i === agora ? "agora" : "";
      li.toggleAttribute("aria-current", i === agora);
    });
    const tentativa = texto.match(/\(tentativa .*\)/);
    lista.children[1].textContent = /^Corrigindo/.test(texto) ? `Corrigindo as instruções ${tentativa?.[0] ?? ""}` : nomes[1];
    el.querySelector(".espera-barra span").style.width = `${((agora + 0.5) / nomes.length) * 100}%`;
    rolar();
  };
  return { etapa, tempo: el.querySelector(".espera-tempo") };
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
    if (atual.ocupado) return;
    executar.disabled = cancelar.disabled = true;
    el.classList.add("feito");
    el.querySelector(".codigo-topo span").textContent = rotulo;
    await pedir((avisos) => fluxo(`/api/${acao}`, { conversa: atual.id }, avisos));
  };
  executar.addEventListener("click", () => decidir("executar", DECISAO.executado));
  cancelar.addEventListener("click", () => decidir("cancelar", DECISAO.cancelado));
  return el;
}

// Erro com o que fazer (telas 13d e erros): título, explicação, passos e um botão que tenta de novo.
function cartaoProblema({ titulo, texto, passos = [], botao, tentar, detalhe = "" }) {
  const el = $("#tpl-problema").content.firstElementChild.cloneNode(true);
  el.querySelector(".problema-titulo").textContent = titulo;
  el.querySelector(".problema-texto").textContent = texto;
  const ol = el.querySelector("ol");
  ol.replaceChildren(...passos.map((passo) => Object.assign(document.createElement("li"), { textContent: passo })));
  ol.hidden = !passos.length;
  const acao = el.querySelector(".problema-botoes button");
  acao.textContent = botao;
  acao.hidden = !tentar;
  acao.addEventListener("click", () => {
    if (atual?.ocupado) return;
    acao.disabled = true;
    tentar();
  });
  el.querySelector(".problema-detalhe").textContent = detalhe;
  el.querySelector("details").hidden = !detalhe;
  return el;
}

const PARA_EU = { Explicar: "olhar a sua cena", Ensinar: "te ensinar no Blender", Verificar: "conferir o seu trabalho" };

function blenderFechado(r, modoDaResposta, pedido, corpo) {
  const acao = modoDaResposta === "Demonstrar" ? `montar ${objetoDoPedido(pedido).o}` : PARA_EU[modoDaResposta];
  return cartaoProblema({
    titulo: "O Blender está fechado",
    texto: `Para eu ${acao}, o Blender precisa estar aberto neste computador.`,
    passos: ["Abra o Blender 5.1.", "Espere ele carregar e clique no botão abaixo."],
    botao: "Já abri, tentar de novo",
    tentar: pedido && (() => mandar(pedido, modoDaResposta, corpo)),
    detalhe: r.texto,
  });
}

function mostrarErro(corpo, texto, tentar) {
  const conexao = texto === SEM_CONEXAO;
  corpo.replaceChildren(cartaoProblema({
    titulo: conexao ? "Perdi a conexão com o Modelia" : "Não consegui responder",
    texto: conexao ? texto : "Tente de novo. Se continuar dando errado, chame o professor.",
    botao: "Tentar de novo", tentar, detalhe: conexao ? "" : texto,
  }));
}

function marcarDecisao(corpo, decisao) {
  const el = corpo.querySelector(".codigo");
  if (!el || !decisao) return;
  el.classList.add("feito");
  el.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  el.querySelector(".codigo-topo span").textContent = DECISAO[decisao];
}

function mostrarResposta(corpo, r, modoDaResposta, pedido = "") {
  if (r.problema === "blender_fechado") {
    corpo.replaceChildren(blenderFechado(r, modoDaResposta, pedido, corpo));
    return;
  }
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

// --- cronômetro: quanto tempo já passou desde o pedido ---

function formatarTempo(segundos) {
  const s = Math.max(0, Math.round(segundos));
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
}

function mostrarTempo(corpo, segundos, texto = "Respondido em") {
  const el = corpo.parentElement.querySelector(".tempo");
  el.hidden = false;
  el.classList.remove("rodando");
  el.textContent = `${texto} ${formatarTempo(segundos)}`;
}

// Liga o cronômetro da mensagem (`inicio` em milissegundos). Com `noCartao`, o tempo corre no cartão de
// espera em vez de embaixo da mensagem. Devolve a função que para e mostra o total.
function cronometro(corpo, inicio, noCartao = null) {
  const el = corpo.parentElement.querySelector(".tempo");
  const passou = () => (Date.now() - inicio) / 1000;
  const tique = () => {
    if (noCartao) noCartao.textContent = formatarTempo(passou());
    else el.textContent = `${formatarTempo(passou())} desde o pedido`;
  };
  el.hidden = Boolean(noCartao);
  el.classList.add("rodando");
  tique();
  const id = setInterval(tique, 1000);
  return (texto) => { // sem texto: só para e esconde
    clearInterval(id);
    if (texto) mostrarTempo(corpo, passou(), texto);
    else el.hidden = true;
  };
}

// Envia algo à API e mostra a resposta numa mensagem do Modelia (nova, ou `corpo` ao retomar ou tentar de novo).
// A conversa continua recebendo a resposta mesmo se o aluno abrir outra no meio.
// Devolve null se a resposta retomada já tinha acabado.
async function pedir(chamada, { corpo = null, modoDoPedido = modo, inicio = Date.now(), pedido = "", etapa = "" } = {}) {
  const conversa = atual;
  conversa.ocupado = true;
  atualizarEnviar();
  corpo ??= adicionarModelia(conversa);
  const espera = modoDoPedido === "Demonstrar" ? cartaoEspera(corpo, pedido) : null;
  if (espera) espera.etapa(etapa);
  else if (!corpo.hasChildNodes() || corpo.querySelector(".problema")) pensando(corpo);
  const parar = cronometro(corpo, inicio, espera?.tempo);
  let texto = "Respondido em";
  try {
    const r = await chamada({
      parcial: (parcial) => { if (!espera) escrevendo(corpo, parcial); }, // no "Faz pra mim" o texto é código
      etapa: (nova) => (espera ? espera.etapa(nova) : naEtapa(corpo, nova)),
    });
    if (!r) {
      texto = "";
      return null;
    }
    mostrarResposta(corpo, r, modoDoPedido, pedido);
    conversa.emEnsino = modoDoPedido === "Ensinar" && (Boolean(r.codigo) || conversa.emEnsino);
    if (conversa === atual) ajustarPlaceholder();
    if (r.atualizar_cena) atualizarCena();
    return r;
  } catch (erro) {
    mostrarErro(corpo, await textoDoErro(erro), () => pedir(chamada, { corpo, modoDoPedido, pedido }));
    texto = "Parou depois de";
    return undefined;
  } finally {
    parar(texto);
    conversa.ocupado = false;
    atualizarEnviar();
    rolar();
  }
}

// Manda uma mensagem do aluno ao agente (também usado por "tentar de novo", com o `corpo` da tentativa).
function mandar(texto, modoDoPedido = modo, corpo = null) {
  return pedir((avisos) => fluxo("/api/mensagem", {
    conversa: atual.id, texto, modo: modoDoPedido, modelo,
    tarefa: modoDoPedido === "Verificar" ? $("#tarefa").value : "",
  }, avisos), { corpo, modoDoPedido, pedido: texto });
}

// "Faça uma cadeira" sem ter escolhido o modo: aprender passo a passo ou receber pronta? (tela 13c)
const PEDE_OBJETO = /^\s*(fa[zç]a?|cri[ae]|mont[ae]|model[ae]|constr[oó]i)\b/i;

function perguntarModo(texto) {
  const corpo = adicionarModelia();
  const el = $("#tpl-escolha").content.firstElementChild.cloneNode(true);
  const objeto = objetoDoPedido(texto);
  el.querySelector("p").textContent =
    `Você quer aprender a fazer ${objeto.o} passo a passo, ou quer que eu faça ${objeto.inteiro} para você?`;
  const botoes = el.querySelectorAll("button");
  for (const botao of botoes) {
    botao.addEventListener("click", () => {
      if (atual?.ocupado) return;
      botoes.forEach((b) => {
        b.disabled = true;
        b.setAttribute("aria-pressed", b === botao);
      });
      modoEscolhido = true;
      escolherModo(botao.dataset.modo);
      mandar(texto);
    });
  }
  corpo.append(el);
  rolar();
}

async function enviar(texto) {
  texto = texto.trim();
  if (!texto || atual?.ocupado) return;
  if (!atual) novaConversaLocal();
  if (!atual.id) {
    try {
      atual.id = (await api("/api/conversas")).conversa;
      guardarAberta(atual.id);
    } catch (erro) {
      alert(`Não consegui começar a conversa. ${await textoDoErro(erro)}`);
      return;
    }
  }
  // Mensagem nova cancela o código que esperava o clique (o servidor faz o mesmo).
  for (const el of atual.elemento.querySelectorAll(".codigo:not(.feito)")) {
    el.classList.add("feito");
    el.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    el.querySelector(".codigo-topo span").textContent = "Não executado";
  }
  for (const b of atual.elemento.querySelectorAll(".escolha button:not(:disabled)")) b.disabled = true;
  if (!atual.titulo) registrarNoHoje(texto);
  $("#boas-vindas").hidden = true;
  adicionarAluno(texto);
  if (!modoEscolhido && modo === "Ensinar" && !atual.emEnsino && PEDE_OBJETO.test(texto)) {
    perguntarModo(texto);
    return;
  }
  await mandar(texto);
}

// --- conversas da sessão (lista "Suas conversas de hoje") ---

function novoElemento() {
  const elemento = document.createElement("div");
  elemento.className = "conversa";
  return elemento;
}

function novaConversaLocal() {
  atual?.elemento.remove();
  atual = { id: "", titulo: "", elemento: novoElemento(), emEnsino: false, ocupado: false };
  $("#centro").append(atual.elemento);
  $("#boas-vindas").hidden = false;
  ajustarPlaceholder();
  guardarAberta("");
  marcarHoje();
  atualizarEnviar();
}

function noHoje(conversa, texto = conversa.titulo) {
  conversas.unshift(conversa);
  const botao = document.createElement("button");
  botao.type = "button";
  botao.className = "item";
  botao.textContent = conversa.titulo;
  botao.title = texto;
  botao.addEventListener("click", () => abrirConversa(conversa));
  conversa.botao = botao;
  $("#hoje .grupo-titulo").after(botao);
  $("#hoje").hidden = false;
}

function registrarNoHoje(texto) {
  atual.titulo = texto.length > 40 ? texto.slice(0, 38) + "…" : texto;
  noHoje(atual, texto);
  marcarHoje();
}

async function abrirConversa(conversa) {
  if (conversa === atual) return;
  atual.elemento.remove(); // uma conversa ainda vazia some; as outras continuam na lista
  atual = conversa;
  guardarAberta(conversa.id);
  const nova = !conversa.elemento;
  conversa.elemento ??= novoElemento();
  $("#centro").append(conversa.elemento);
  $("#boas-vindas").hidden = true;
  marcarHoje();
  fecharMenu();
  if (nova) await restaurar(conversa);
  ajustarPlaceholder();
  atualizarEnviar();
  rolar();
}

// Monta uma conversa guardada no servidor (ao voltar de outra tela) e retoma a resposta em andamento.
async function restaurar(conversa) {
  let d;
  try {
    const r = await fetch(`/api/conversas/${conversa.id}`);
    if (!r.ok) throw new Error();
    d = await r.json();
  } catch {
    conversa.elemento.innerHTML = '<p class="erro">Não consegui abrir esta conversa.</p>';
    return;
  }
  let emAndamento = null;
  let pedido = "";
  for (const item of d.historico) {
    if (item.tipo === "aluno") {
      adicionarAluno(item.texto, conversa);
      pedido = item.texto;
      continue;
    }
    const corpo = adicionarModelia(conversa);
    if (item.resposta) {
      mostrarResposta(corpo, item.resposta, item.modo, pedido);
      marcarDecisao(corpo, item.decisao);
      if (item.segundos != null) mostrarTempo(corpo, item.segundos);
      conversa.emEnsino = item.modo === "Ensinar" && (Boolean(item.resposta.codigo) || conversa.emEnsino);
    } else if (item.erro) {
      const doPedido = pedido;
      mostrarErro(corpo, item.erro, doPedido && (() => mandar(doPedido, item.modo, corpo)));
      if (item.segundos != null) mostrarTempo(corpo, item.segundos, "Parou depois de");
    } else {
      if (item.modo !== "Demonstrar") { // o "Faz pra mim" monta o cartão de espera em pedir()
        if (item.etapa) naEtapa(corpo, item.etapa);
        else pensando(corpo);
        if (item.parcial) escrevendo(corpo, item.parcial);
      }
      emAndamento = { corpo, modoDoPedido: item.modo, inicio: item.inicio * 1000, pedido, etapa: item.etapa };
    }
  }
  if (emAndamento) {
    const acompanhar = (avisos) => fluxo(`/api/conversas/${conversa.id}/acompanhar`, undefined, avisos);
    pedir(acompanhar, emAndamento).then((r) => {
      if (r === null) { // acabou entre buscar a conversa e retomar: monta de novo, já completa
        conversa.elemento.replaceChildren();
        restaurar(conversa);
      }
    });
  }
}

async function carregarConversas(abertaId) {
  let lista = [];
  try {
    lista = await (await fetch("/api/conversas")).json();
  } catch {
    return;
  }
  for (const c of lista.reverse()) noHoje({ id: c.conversa, titulo: c.titulo, elemento: null, emEnsino: false, ocupado: false });
  const aberta = conversas.find((c) => c.id === abertaId);
  if (aberta) await abrirConversa(aberta);
  else marcarHoje();
}

function marcarHoje() {
  for (const c of conversas) c.botao.setAttribute("aria-current", c === atual);
}

// --- modo, tarefa e painel ---

function ajustarPlaceholder() {
  $("#texto").placeholder = atual?.emEnsino && modo === "Ensinar" ? NO_ENSINO : PLACEHOLDER;
}

function escolherModo(novo) {
  modo = novo;
  document.body.dataset.modo = novo;
  for (const b of document.querySelectorAll(".lateral [data-modo]")) b.setAttribute("aria-current", b.dataset.modo === novo);
  for (const b of document.querySelectorAll(".modos button")) b.setAttribute("aria-pressed", b.dataset.modo === novo);
  $("#tarefa-rotulo").hidden = novo !== "Verificar";
  ajustarPlaceholder();
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
      viewport.innerHTML = "<p>O Blender está fechado. Abra o Blender 5.1 para ver a cena aqui.</p>";
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
  const ocupado = Boolean(atual?.ocupado);
  $("#enviar").disabled = ocupado || !temTexto;
  $("#centro").setAttribute("aria-busy", ocupado);
  $("#enviar").classList.toggle("pronto", temTexto && !ocupado);
}

function fecharMenu() {
  $("#lateral").classList.remove("aberta");
}

// --- ligações ---

for (const b of document.querySelectorAll(".lateral [data-modo], .modos button")) {
  b.addEventListener("click", () => {
    modoEscolhido = true;
    escolherModo(b.dataset.modo);
    fecharMenu();
    $("#texto").focus();
  });
}
for (const b of document.querySelectorAll(".sugestao")) {
  b.addEventListener("click", () => {
    modoEscolhido = true;
    escolherModo(b.dataset.modo);
    enviar(b.dataset.pedido);
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
  novaConversaLocal();
  fecharMenu();
  $("#texto").focus();
});
$("#tarefa").addEventListener("change", mostrarTarefa);
$("#botao-cena").addEventListener("click", () => alternarPainel());
$("#atualizar").addEventListener("click", atualizarCena);
$("#menu").addEventListener("click", () => $("#lateral").classList.toggle("aberta"));

const abertaAntes = location.hash === "#nova" ? "" : lerAberta(); // novaConversaLocal apaga a guardada
novaConversaLocal();
escolherModo(modo);
atualizarEnviar();
carregarConversas(abertaAntes);
vigiarSituacao();
if (matchMedia("(min-width: 1181px)").matches) alternarPainel(true);
