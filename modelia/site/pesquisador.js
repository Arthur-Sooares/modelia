// Telas do Pesquisador. Conversa com /api/pesquisador (modelia/pesquisador.py).
// Cada tela tem carregar (busca os dados) e desenhar (monta o HTML com os dados e os trabalhos em andamento).
// O que demora roda no servidor como um "trabalho"; esta página consulta /trabalhos para mostrar a barra.

const $ = (seletor) => document.querySelector(seletor);
const TELAS = ["modelos", "conhecimento", "historico", "experimento", "instalacao"];
const MODOS = ["Explicar", "Ensinar", "Demonstrar", "Verificar"];
// Cor dos números em Histórico: as versões "-texto" têm contraste suficiente sobre o cartão claro.
const COR_MODO = { Explicar: "var(--azul)", Ensinar: "var(--laranja-texto)", Demonstrar: "var(--laranja-texto)", Verificar: "var(--verde-texto)" };

let senha = lerSenha();
let tela = "";
let trabalhos = {};
const dados = {};

// --- utilidades ---

function lerSenha() {
  try { return sessionStorage.getItem("modelia-senha") || ""; } catch { return ""; }
}
function guardarSenha(valor) {
  senha = valor;
  try { sessionStorage.setItem("modelia-senha", valor); } catch { /* aba anônima: pede de novo depois */ }
}
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const num = (n) => Number(n).toLocaleString("pt-BR", { maximumFractionDigits: 1 });
const gb = (n) => `${num(n)} GB`;
const dataBr = (iso) => (iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—");

function barra(fracao, texto) {
  const indefinida = fracao == null;
  return `<div class="progresso">
    <p class="progresso-texto"><span>${esc(texto)}</span>${indefinida ? "" : `<span>${Math.round(fracao * 100)}%</span>`}</p>
    <div class="barra-progresso${indefinida ? " indefinida" : ""}"><span style="width:${indefinida ? 30 : Math.round(fracao * 100)}%"></span></div>
  </div>`;
}

function tabela(colunas, linhas, vazio) {
  if (!linhas.length) return `<div class="vazio">${vazio}</div>`;
  return `<table><thead><tr>${colunas.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${linhas.join("")}</tbody></table>`;
}

let tempoAviso;
function avisar(texto) {
  const toast = $("#toast");
  toast.textContent = texto;
  toast.hidden = false;
  clearTimeout(tempoAviso);
  tempoAviso = setTimeout(() => { toast.hidden = true; }, 6000);
}

// O navegador avisa queda de conexão com um TypeError em inglês ("network error", "Failed to fetch").
// O navegador avisa queda de conexão com um TypeError em inglês ("Failed to fetch"), mas um erro na própria
// página também é TypeError: antes de culpar a conexão, confere se o servidor responde.
async function textoDoErro(erro) {
  if (!(erro instanceof TypeError)) return erro.message;
  const vivo = await fetch("/api/situacao").then((r) => r.ok, () => false);
  return vivo ? `Não consegui fazer isso (${erro.message}). O Modelia continua ligado: tente de novo.`
    : "Perdi a conexão com o Modelia. Confira se o terminal com “uv run main.py” ainda está aberto e tente de novo.";
}

async function tentar(acao) {
  try {
    await acao();
  } catch (erro) {
    avisar(await textoDoErro(erro));
  }
}

// Confere o arquivo antes de mandar. O navegador não consegue enviar uma pasta (falha sem explicar).
function problemaNoArquivo(arquivo) {
  if (!arquivo) return "Escolha um arquivo PDF, Markdown ou texto.";
  if (!arquivo.size && !arquivo.type) {
    return "Isso parece uma pasta. Se for o manual do Blender já extraído, use o botão “Usar uma pasta” no cartão do manual. Aqui vão só arquivos PDF, .md ou .txt.";
  }
  if (/\.zip$/i.test(arquivo.name)) return "Para o manual do Blender, use o botão “Já tenho o .zip” no cartão do manual, logo abaixo.";
  if (!/\.(pdf|md|txt)$/i.test(arquivo.name)) return "Use um arquivo PDF, Markdown (.md) ou texto (.txt).";
  return "";
}

// --- API (pede a senha só se o servidor tiver MODELIA_SENHA) ---

let pedindoSenha = null;
function pedirSenha(errada) {
  pedindoSenha ??= new Promise((resolve) => {
    $("#senha-erro").hidden = !errada;
    $("#senha").value = "";
    $("#entrar").showModal();
    $("#form-entrar").addEventListener("submit", () => {
      guardarSenha($("#senha").value);
      pedindoSenha = null;
      resolve();
    }, { once: true });
  });
  return pedindoSenha;
}

async function chamar(caminho, { metodo = "GET", json, form } = {}) {
  const opcoes = { method: metodo, headers: { "X-Senha": senha } };
  if (json) {
    opcoes.headers["Content-Type"] = "application/json";
    opcoes.body = JSON.stringify(json);
  }
  if (form) opcoes.body = form;
  const resposta = await fetch(`/api/pesquisador${caminho}`, opcoes);
  if (resposta.status === 401) {
    await pedirSenha(Boolean(senha));
    return chamar(caminho, { metodo, json, form });
  }
  if (!resposta.ok) {
    const corpo = await resposta.json().catch(() => ({}));
    throw new Error(typeof corpo.detail === "string" ? corpo.detail : `Erro ${resposta.status}`);
  }
  return resposta;
}
const ler = async (caminho) => (await chamar(caminho)).json();
const postar = (caminho, json) => chamar(caminho, { metodo: "POST", json: json ?? {} });

// Começa um trabalho longo no servidor e já mostra a barra.
async function comecar(caminho, { json, form } = {}) {
  await chamar(caminho, { metodo: "POST", json: form ? undefined : (json ?? {}), form });
  await acompanhar();
}

async function acompanhar() {
  let novos;
  try {
    novos = await ler("/trabalhos");
  } catch {
    return;
  }
  const terminaram = Object.keys(novos).filter((k) => trabalhos[k]?.ativo && !novos[k].ativo);
  trabalhos = novos;
  for (const k of terminaram) avisar(novos[k].erro ? `Parou: ${novos[k].erro}` : novos[k].texto);
  if (terminaram.length) {
    carregar();
    situacao();
  } else {
    desenhar();
  }
}

async function vigiar() {
  await acompanhar();
  setTimeout(vigiar, Object.values(trabalhos).some((t) => t.ativo) ? 1000 : 4000);
}

async function situacao() {
  try {
    const s = await (await fetch("/api/situacao")).json();
    $("#estado-ollama").classList.toggle("desligado", !s.ollama);
    $("#estado-blender").classList.toggle("desligado", !s.blender);
  } catch {
    $("#estado-ollama").classList.add("desligado");
  }
}

// --- 8 · Modelos ---

async function carregarModelos() {
  dados.modelos = await ler("/modelos");
  desenharModelos();
}

function desenharModelos() {
  const d = dados.modelos;
  if (!d) return;
  $("#modelos-sub").textContent = `LLMs instaladas no Ollama deste computador. A placa tem ${gb(d.vram_gb)} de VRAM.`;
  $("#modelos-aviso").innerHTML = d.ollama ? "" : `<div class="aviso-faixa laranja">
      <span>O Ollama está desligado. Sem ele não dá para ver, baixar nem usar os modelos.</span>
      <button class="botao laranja" type="button" data-acao="ligar">Ligar Ollama</button>
      <a class="botao cinza" href="#instalacao">Instalação</a></div>`;
  $("#modelos-vram").hidden = !d.ollama;
  $("#modelos-vram").innerHTML = `<p>Memória de vídeo em uso agora<span>${num(d.vram_em_uso)} de ${num(d.vram_gb)} GB</span></p>
    <div class="barra-progresso"><span style="width:${Math.min(100, (d.vram_em_uso / d.vram_gb) * 100)}%"></span></div>`;

  $("#modelos-instalados").innerHTML = tabela(["Modelo", "Tamanho", "Cabe na VRAM?", "Uso", ""], d.instalados.map((m) => `<tr>
      <td>${esc(m.nome)}</td>
      <td class="mono">${gb(m.tamanho_gb)}</td>
      <td>${m.cabe ? '<span class="pil sim">sim</span>' : '<span class="pil falta">não, usa a RAM</span>'}</td>
      <td>${m.padrao ? '<span class="pil azul">padrão do chat</span>' : m.embeddings ? '<span class="pil cinza">embeddings</span>'
        : !m.ferramentas ? '<span class="pil falta" title="Não sabe chamar ferramentas (tools): não mexe no Blender">sem ferramentas</span>' : "—"}</td>
      <td class="botoes">${m.padrao || m.embeddings ? "" : `
        ${m.ferramentas ? `<button class="botao cinza" type="button" data-acao="padrao" data-nome="${esc(m.nome)}">Usar como padrão</button>` : ""}
        <button class="botao perigo" type="button" data-acao="apagar" data-nome="${esc(m.nome)}">Apagar</button>`}</td>
    </tr>`), d.ollama ? "<strong>Nenhum modelo instalado</strong>Baixe um modelo abaixo ou prepare tudo na tela Instalação."
    : "<strong>Ollama desligado</strong>Ligue o Ollama para ver os modelos instalados.");

  const trabalho = trabalhos.modelo?.ativo ? trabalhos.modelo : null;
  let sugestoes = d.sugestoes;
  if (trabalho && !sugestoes.some((s) => s.nome === trabalho.nome)) {
    sugestoes = [{ nome: trabalho.nome, descricao: "Baixando pelo nome digitado.", tamanho_gb: null }, ...sugestoes];
  }
  $("#modelos-sugestoes").innerHTML = sugestoes.map((s) => {
    const baixando = trabalho?.nome === s.nome;
    const cabe = s.tamanho_gb == null ? "" : s.cabe ? '<span class="pil sim">cabe na VRAM</span>' : '<span class="pil falta">não cabe na VRAM</span>';
    return `<div class="cartao sugestao-modelo${baixando ? " ativo" : ""}">
      <div class="topo"><strong>${esc(s.nome)}</strong>${cabe}</div>
      <p>${esc(s.descricao)}</p>
      ${baixando ? barra(trabalho.fracao, trabalho.texto) : `<div class="base"><span>${s.tamanho_gb == null ? "" : gb(s.tamanho_gb)}</span>
        <button class="botao cinza" type="button" data-acao="baixar" data-nome="${esc(s.nome)}" ${!d.ollama || trabalho ? "disabled" : ""}>Baixar</button></div>`}
    </div>`;
  }).join("");
}

// --- 9 · Conhecimento ---

async function carregarConhecimento() {
  dados.conhecimento = await ler("/conhecimento");
  desenharConhecimento();
}

function desenharConhecimento() {
  const d = dados.conhecimento;
  if (!d) return;
  const m = d.manual;
  const instalacao = trabalhos.instalacao?.ativo ? trabalhos.instalacao : null;
  const indexando = instalacao?.etapa === "manual";
  const vazia = !m.na_base && !d.documentos.length;
  const desligar = instalacao ? "disabled" : "";
  const botoes = m.faltam ? `<button class="botao laranja" type="button" data-acao="baixar-manual" ${desligar}>Completar o manual</button>`
    : m.na_base
    ? `<button class="botao cinza" type="button" data-acao="atualizar-manual" ${desligar}>Atualizar</button>
       <button class="botao perigo" type="button" data-acao="remover-manual" ${desligar}>Remover</button>`
    : `<button class="botao cinza" type="button" data-acao="pasta-manual" ${desligar}>Usar uma pasta</button>
       <button class="botao cinza" type="button" data-acao="zip-manual" ${desligar}>Já tenho o .zip</button>
       <button class="botao ${vazia ? "laranja" : "cinza"}" type="button" data-acao="baixar-manual" ${desligar}>${m.embutido ? "Indexar o manual" : "Baixar do site"}</button>`;
  $("#manual-cartao").innerHTML = `<div class="cartao manual${indexando ? " ativo" : vazia ? " destaque" : ""}">
    <div>
      <h3>Manual do Blender ${esc(m.versao)} ${m.faltam ? '<span class="pil falta">incompleto</span>' : m.na_base ? '<span class="pil sim">na base</span>' : '<span class="pil falta">fora da base</span>'}</h3>
      <p>${m.faltam ? `A indexação parou no meio: faltam ${m.faltam} de ${m.paginas + m.faltam} páginas. Clique em “Completar o manual” para continuar de onde parou.`
        : m.na_base
        ? `${m.paginas} páginas de interface, modelagem e modificadores viraram ${m.trechos.toLocaleString("pt-BR")} trechos.${m.adicionado ? ` Indexado em ${dataBr(m.adicionado)}.` : ""}`
        : m.embutido ? `Base do modo Explicar: páginas de interface, modelagem e modificadores, em português. O texto do manual ${esc(m.versao)} já vem com o Modelia: é só indexar, sem internet. Para outra versão, use uma pasta ou o .zip.`
          : "Base do modo Explicar: páginas de interface, modelagem e modificadores, em português. Baixe do site oficial (~350 MB), use o .zip ou a pasta que você já tem."}</p>
      <p class="mono">${esc(m.endereco.replace(/^https:\/\/|\/$/g, ""))} · CC BY-SA 4.0</p>
      ${indexando ? barra(instalacao.fracao, instalacao.texto) : ""}
    </div>
    ${indexando ? "" : `<div class="acoes">${botoes}</div>`}
  </div>`;

  const documento = trabalhos.documento?.ativo ? trabalhos.documento : null;
  $("#documento-progresso").innerHTML = documento ? `<p class="aviso-faixa"><span class="gira"></span>${esc(documento.texto)}</p>` : "";
  $("#form-documento button[type=submit]").disabled = Boolean(documento);

  $("#documentos").innerHTML = tabela(["Título", "Fonte", "Trechos", "Adicionado", ""], d.documentos.map((doc) => `<tr>
      <td>${esc(doc.titulo)}</td><td>${esc(doc.fonte) || "—"}</td>
      <td class="mono">${doc.trechos}</td><td class="mono">${dataBr(doc.adicionado)}</td>
      <td class="botoes"><button class="botao perigo" type="button" data-acao="remover-doc" data-id="${esc(doc.doc_id)}"
        data-nome="${esc(doc.titulo)}">Remover</button></td></tr>`),
  "<strong>Nenhum documento ainda</strong>Adicione a apostila ou outro material da disciplina acima.");
}

function mostrarArquivo() {
  const arquivo = $("#arquivo").files[0];
  $("#soltar-nome").textContent = arquivo ? arquivo.name : "Arraste um arquivo ou clique para escolher";
}

// --- 10 · Histórico ---

function filtros() {
  return new URLSearchParams({ estudante: $("#f-estudante").value, modo: $("#f-modo").value, desde: $("#f-desde").value, ate: $("#f-ate").value });
}

async function carregarHistorico() {
  const d = await ler(`/historico?${filtros()}`);
  const escolhido = $("#f-estudante").value;
  $("#f-estudante").replaceChildren(new Option("Todos", ""), ...d.estudantes.map((e) => new Option(e, e, false, e === escolhido)));
  const contadores = [[d.total, "Interações", "var(--tinta)"], ...MODOS.map((m) => [d.por_modo[m], m, COR_MODO[m]]),
    [d.erros, "Erros do Blender", "#b5470a"]];
  $("#contadores").innerHTML = contadores.map(([n, rotulo, cor]) =>
    `<div class="contador"><strong style="color:${cor}">${n}</strong><span>${rotulo}</span></div>`).join("");
  $("#exportar").disabled = !d.total;
  const hora = (iso) => `${iso.slice(8, 10)}/${iso.slice(5, 7)} ${iso.slice(11, 16)}`;
  $("#interacoes").innerHTML = tabela(["Data", "Participante", "Modo", "Pedido", "Modelo", "Tempo"], d.linhas.map((l) => `<tr>
      <td class="mono">${hora(l.data)}</td><td class="mono">${esc(l.estudante)}</td>
      <td><span class="pil ${esc(l.modo)}">${esc(l.modo)}</span></td>
      <td class="longo" title="${esc(l.pedido)}">${esc(l.pedido)}</td><td>${esc(l.modelo)}</td>
      <td class="mono">${Math.round(l.segundos)} s</td></tr>`),
  "<strong>Sem registros</strong>Nenhuma interação com esses filtros. As conversas do chat aparecem aqui.") +
    (d.total > d.linhas.length ? `<p class="obs vazio">Mostrando as ${d.linhas.length} mais recentes de ${d.total}. O CSV leva todas.</p>` : "");
}

async function exportar() {
  const resposta = await chamar(`/historico.csv?${filtros()}`);
  const nome = /filename="(.+?)"/.exec(resposta.headers.get("Content-Disposition") || "")?.[1] || "modelia_historico.csv";
  const link = document.createElement("a");
  link.href = URL.createObjectURL(await resposta.blob());
  link.download = nome;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

// --- 11 · Experimento ---

const escolhidos = { modelos: new Set(), tarefas: new Set(), condicoes: new Set() };

async function carregarExperimento() {
  const primeiraVez = !dados.experimento;
  const d = dados.experimento = await ler("/experimento");
  if (primeiraVez) d.condicoes.forEach((c) => escolhidos.condicoes.add(c));
  fichas("#exp-modelos", d.modelos.map((m) => [m, m]), "modelos", "Nenhum modelo de chat instalado. Baixe na tela Modelos.");
  fichas("#exp-tarefas", d.tarefas.map((t) => [t.id, t.nome]), "tarefas");
  fichas("#exp-condicoes", d.condicoes.map((c) => [c, c]), "condicoes");
  $("#cena-base").textContent = d.cena_base ? "Recriar cena base" : "Criar cena base";
  previstas();
  desenharExperimento();
}

function fichas(seletor, itens, grupo, vazio = "") {
  const caixa = $(seletor);
  for (const valor of [...escolhidos[grupo]]) if (!itens.some(([v]) => v === valor)) escolhidos[grupo].delete(valor);
  caixa.replaceChildren(...itens.map(([valor, rotulo]) => {
    const botao = document.createElement("button");
    botao.type = "button";
    botao.className = "ficha";
    botao.textContent = rotulo;
    botao.setAttribute("aria-pressed", escolhidos[grupo].has(valor));
    botao.addEventListener("click", () => {
      if (!escolhidos[grupo].delete(valor)) escolhidos[grupo].add(valor);
      botao.setAttribute("aria-pressed", escolhidos[grupo].has(valor));
      previstas();
    });
    return botao;
  }));
  if (!itens.length && vazio) caixa.innerHTML = `<span class="obs">${vazio}</span>`;
}

const seeds = () => $("#exp-seeds").value.split(",").map((s) => parseInt(s.trim(), 10)).filter((n) => !Number.isNaN(n));

function previstas() {
  const repeticoes = Math.max(0, parseInt($("#exp-repeticoes").value, 10) || 0);
  $("#exp-previstas").textContent = escolhidos.modelos.size * escolhidos.tarefas.size * escolhidos.condicoes.size * repeticoes;
  $("#exp-temperatura-valor").textContent = num($("#exp-temperatura").value);
}

function desenharExperimento() {
  const d = dados.experimento;
  if (!d) return;
  const t = trabalhos.experimento;
  $("#iniciar").disabled = Boolean(t?.ativo);
  $("#parar").disabled = !t?.ativo;
  $("#cena-base").disabled = Boolean(t?.ativo);
  let progresso;
  if (t) {
    progresso = `<div class="cartao progresso-exp${t.ativo ? " ativo" : ""}">
      ${barra(t.ativo ? t.fracao : 1, t.texto)}
      ${t.erro ? `<p class="erro">${esc(t.erro)}</p>` : ""}
      ${t.arquivo ? `<p class="obs">O que já foi gravado fica salvo em ${esc(t.arquivo)}</p>` : ""}</div>`;
  } else {
    const falta = [!d.blender && "Abra o Blender 5.1 com o add-on MCP ligado.",
      !d.cena_base && "Crie a cena base antes de iniciar (botão no alto)."].filter(Boolean);
    progresso = `<div class="cartao"><p class="sub">Nada rodando. Escolha os modelos e as tarefas e clique em Iniciar.</p>
      ${falta.map((f) => `<p class="erro">${f}</p>`).join("")}</div>`;
  }
  $("#exp-progresso").innerHTML = progresso;
  const linhas = t?.linhas || [];
  $("#exp-linhas").innerHTML = tabela(["Condição", "Modelo", "Tarefa", "Rep.", "Nota", "Observação"], linhas.map((l) => `<tr>
      <td>${esc(l.condicao)}</td><td class="mono">${esc(l.modelo)}</td><td class="mono">${esc(l.tarefa)}</td>
      <td class="mono">${esc(l.repeticao)}</td><td><span class="obs-exp n${esc(l.nota_0a3)}">${esc(l.nota_0a3)} / 3</span></td>
      <td>${esc(l.avaliacao)}</td></tr>`), "As linhas entram uma por uma enquanto o experimento roda.");
}

// --- 12 · Instalação ---

async function carregarInstalacao() {
  dados.instalacao = await ler("/instalacao");
  desenharInstalacao();
}

function desenharInstalacao() {
  const d = dados.instalacao;
  if (!d) return;
  const t = trabalhos.instalacao;
  const rodando = t?.ativo ? t : null;
  const [chat, embeddings] = d.necessarios;
  const embeddingsOk = d.ollama_ligado && !d.modelos_faltando.includes(embeddings);
  const pronto = { ollama: d.ollama_ligado, modelos: d.ollama_ligado && !d.modelos_faltando.length, manual: d.manual };
  const desligar = rodando ? "disabled" : "";
  const botao = (rotulo, atributos, cor = "cinza", travado = false) =>
    `<button class="botao ${cor}" type="button" ${atributos} ${rodando || travado ? "disabled" : ""}>${rotulo}</button>`;

  const etapas = [
    {
      nome: "ollama", titulo: "Ollama",
      pilula: d.ollama_ligado ? ["sim", "instalado e ligado"] : d.ollama_instalado ? ["falta", "desligado"] : ["falta", "não encontrado"],
      texto: d.ollama_ligado ? "Ligado em 127.0.0.1:11434. O Modelia liga o Ollama sozinho quando abre."
        : d.ollama_instalado ? "Está instalado, mas desligado. O Modelia tenta ligar quando abre; ou clique em Ligar."
          : "Programa que roda a IA neste computador, sem internet. O Modelia baixa o instalador oficial (~1 GB) e instala só para este usuário, sem pedir senha de administrador.",
      mono: "ollama.com/download · Windows", codigo: true,
      botoes: pronto.ollama ? "" : d.ollama_instalado ? botao("Ligar Ollama", 'data-acao="ligar"', "laranja")
        : botao("Instalar Ollama", 'data-acao="etapa" data-etapa="ollama"', "laranja"),
    },
    {
      nome: "modelos", titulo: "Modelos de IA",
      pilula: pronto.modelos ? ["sim", "prontos"] : ["falta", `faltam ${d.modelos_faltando.length}`],
      texto: `${chat} conversa com o aluno. ${embeddings} busca os trechos do manual. Os dois cabem numa placa de 6 GB.`,
      mono: !d.ollama_ligado ? "Disponível depois da etapa 1" : pronto.modelos ? "Tudo instalado" : `Faltam: ${d.modelos_faltando.join(", ")}`,
      travado: !d.ollama_ligado,
      botoes: pronto.modelos ? "" : botao("Baixar modelos", 'data-acao="etapa" data-etapa="modelos"', "cinza", !d.ollama_ligado),
    },
    {
      nome: "manual", titulo: `Manual do Blender ${d.manual_versao}`,
      pilula: d.manual ? ["sim", "na base"] : ["falta", "fora da base"],
      texto: d.manual_embutido
        ? "Base do modo Explicar: páginas de interface, modelagem e modificadores, em português. O texto já vem com o Modelia: é só indexar, sem internet."
        : "Base do modo Explicar: páginas de interface, modelagem e modificadores, em português. Baixe do site oficial (~350 MB) ou use o .zip que você já tem.",
      mono: embeddingsOk || d.manual ? `${d.manual_endereco.replace(/^https:\/\/|\/$/g, "")} · CC BY-SA 4.0` : "Disponível depois da etapa 2 (precisa do modelo de embeddings)",
      codigo: embeddingsOk || d.manual, travado: !embeddingsOk,
      botoes: d.manual ? "" : botao("Usar uma pasta", 'data-acao="pasta-manual"', "cinza", !embeddingsOk) +
        botao("Já tenho o .zip", 'data-acao="zip-manual"', "cinza", !embeddingsOk) +
        botao(d.manual_embutido ? "Indexar o manual" : "Baixar do site", 'data-acao="baixar-manual"', "cinza", !embeddingsOk),
    },
  ];

  const sequencia = rodando?.sequencia || [];
  $("#etapas").innerHTML = etapas.map((e, i) => {
    const atual = rodando?.etapa === e.nome;
    const naFila = rodando && !atual && sequencia.indexOf(e.nome) > sequencia.indexOf(rodando.etapa);
    const falhou = !rodando && t?.erro && t.etapa === e.nome;
    const pilula = atual ? ["azul", "baixando"] : naFila ? ["cinza", "na fila"] : falhou ? ["falta", "parou"] : e.pilula;
    const feito = pronto[e.nome] && !atual;
    return `<div class="cartao etapa${atual ? " ativo" : ""}">
      <span class="etapa-numero${feito ? " feito" : ""}">${feito ? "✓" : i + 1}</span>
      <div class="etapa-corpo">
        <h3>${esc(e.titulo)} <span class="pil ${pilula[0]}">${esc(pilula[1])}</span></h3>
        <p>${atual && e.nome === "manual" && !d.manual ? "Baixando e indexando. Precisa do modelo de embeddings ligado." : esc(e.texto)}</p>
        <p class="${e.codigo ? "mono" : "obs"}">${esc(e.mono)}</p>
        ${atual ? barra(rodando.fracao, rodando.texto) : ""}
        ${falhou ? `<p class="etapa-erro">${esc(t.erro)}</p>` : ""}
      </div>
      ${atual || naFila ? "" : `<div class="acoes">${falhou ? botao("Tentar de novo", `data-acao="etapa" data-etapa="${e.nome}"`, "laranja", e.travado) : e.botoes}</div>`}
    </div>`;
  }).join("");

  // Opcional, fora da contagem das 3 etapas: o atalho que abre o Modelia com dois cliques (tela 0 do Figma).
  if (d.atalho.possivel) {
    $("#etapas").insertAdjacentHTML("beforeend", `<div class="cartao etapa">
      <span class="etapa-numero${d.atalho.existe ? " feito" : ""}">${d.atalho.existe ? "✓" : "+"}</span>
      <div class="etapa-corpo">
        <h3>Atalho na área de trabalho <span class="pil ${d.atalho.existe ? "sim" : "cinza"}">${d.atalho.existe ? "criado" : "opcional"}</span></h3>
        <p>Os alunos abrem o Modelia com dois cliques no ícone, sem terminal. Ele mostra “Abrindo o Modelia…” e vai para o chat quando estiver pronto.</p>
        <p class="mono">Área de trabalho · Modelia</p>
      </div>
      <div class="acoes">${botao(d.atalho.existe ? "Criar de novo" : "Criar atalho", 'data-acao="atalho"', d.atalho.existe ? "cinza" : "laranja")}</div>
    </div>`);
  }

  const tudoPronto = pronto.ollama && pronto.modelos && pronto.manual;
  $("#instalacao-sub").textContent = rodando
    ? "Pode continuar usando o computador. Se fechar o Modelia, o download continua de onde parou na próxima vez."
    : tudoPronto ? "Tudo pronto neste computador. Os alunos já podem usar o chat."
      : "O que o Modelia precisa neste computador. É só uma vez: o que já está pronto fica marcado.";
  $("#verificar").disabled = Boolean(rodando);
  $("#instalacao-rodape").innerHTML = rodando
    ? `<button class="botao cinza" type="button" disabled>Preparando…</button><span>Etapa ${["ollama", "modelos", "manual"].indexOf(rodando.etapa) + 1} de 3. A barra de cada etapa mostra o que está sendo baixado agora.</span>`
    : tudoPronto ? '<a class="botao laranja" href="chat.html">Abrir o chat</a><span>Ollama, modelos e manual prontos.</span>'
      : `<button class="botao laranja" type="button" data-acao="etapa" data-etapa="tudo" ${desligar}>Preparar tudo</button><span>Faz as três etapas em sequência, cerca de 5 GB. Se a internet cair, o download continua de onde parou.</span>`;
}

// --- ações dos botões (data-acao) ---

function formManual(campos = {}) {
  const form = new FormData();
  form.append("atualizar", campos.atualizar ? "true" : "false");
  if (campos.arquivo) form.append("arquivo", campos.arquivo);
  return form;
}

const ACOES = {
  async atalho(botao) {
    botao.disabled = true;
    await postar("/atalho");
    avisar("Atalho “Modelia” criado na área de trabalho.");
    await carregar();
  },
  async ligar(botao) {
    botao.disabled = true;
    botao.textContent = "Ligando…";
    await postar("/ollama/ligar");
    await carregar();
    situacao();
  },
  async padrao(botao) {
    await postar("/modelos/padrao", { nome: botao.dataset.nome });
    avisar(`${botao.dataset.nome} agora é o padrão do chat.`);
    await carregar();
  },
  async apagar(botao) {
    if (!confirm(`Apagar ${botao.dataset.nome}? Libera espaço em disco; para usar de novo, é preciso baixar outra vez.`)) return;
    await postar("/modelos/apagar", { nome: botao.dataset.nome });
    await carregar();
  },
  baixar: (botao) => comecar("/modelos/baixar", { json: { nome: botao.dataset.nome } }),
  async "remover-doc"(botao) {
    if (!confirm(`Remover “${botao.dataset.nome}” da base? O modo Explicar deixa de usar esse material.`)) return;
    await postar("/conhecimento/remover", { doc_id: botao.dataset.id });
    await carregar();
  },
  "baixar-manual": () => comecar("/manual", { form: formManual() }),
  "zip-manual": () => $("#zip-manual").click(),
  // O navegador não envia pastas: a pessoa cola o caminho e o servidor (neste computador) lê direto dela.
  async "pasta-manual"() {
    const caminho = prompt("Cole o caminho da pasta do manual já extraído (a que tem o arquivo blender_manual_v…_pt.html).\n\n" +
      "No Explorador de Arquivos, abra a pasta, clique na barra de endereço e copie com Ctrl+C.");
    if (caminho?.trim()) await comecar("/manual/pasta", { json: { caminho } });
  },
  async "atualizar-manual"() {
    if (!confirm("Indexar o manual de novo? As páginas atuais saem da base até a nova indexação terminar.")) return;
    await comecar("/manual", { form: formManual({ atualizar: true }) });
  },
  async "remover-manual"() {
    if (!confirm("Tirar o manual da base? O modo Explicar fica sem ele até indexar de novo.")) return;
    await postar("/manual/remover");
    await carregar();
  },
  etapa: (botao) => comecar(`/instalacao/${botao.dataset.etapa}`),
};

// --- navegação ---

const CARREGAR = { modelos: carregarModelos, conhecimento: carregarConhecimento, historico: carregarHistorico,
  experimento: carregarExperimento, instalacao: carregarInstalacao };
const DESENHAR = { modelos: desenharModelos, conhecimento: desenharConhecimento, experimento: desenharExperimento,
  instalacao: desenharInstalacao };

const carregar = () => tentar(CARREGAR[tela]);
const desenhar = () => DESENHAR[tela]?.();

function abrir() {
  const pedida = location.hash.slice(1);
  tela = TELAS.includes(pedida) ? pedida : "modelos";
  for (const secao of document.querySelectorAll(".tela")) secao.hidden = secao.id !== tela;
  for (const link of document.querySelectorAll('.lateral a[href^="#"]')) link.setAttribute("aria-current", link.getAttribute("href") === `#${tela}`);
  document.title = `Modelia · ${$(`#${tela} h1`).textContent.toLowerCase()}`;
  $("#lateral").classList.remove("aberta");
  $("#pagina").scrollTop = 0;
  carregar();
}

// --- ligações ---

document.addEventListener("click", (e) => {
  const botao = e.target.closest("[data-acao]");
  if (botao && ACOES[botao.dataset.acao]) tentar(() => ACOES[botao.dataset.acao](botao));
});
$("#entrar").addEventListener("cancel", (e) => e.preventDefault()); // sem senha, não há o que mostrar
$("#menu").addEventListener("click", () => $("#lateral").classList.toggle("aberta"));

$("#form-baixar").addEventListener("submit", (e) => {
  e.preventDefault();
  const nome = $("#nome-modelo").value.trim();
  if (!nome) return avisar("Digite o nome do modelo, por exemplo mistral:7b.");
  tentar(async () => {
    await comecar("/modelos/baixar", { json: { nome } });
    $("#nome-modelo").value = "";
  });
});

$("#arquivo").addEventListener("change", mostrarArquivo);
const soltar = $("#soltar");
soltar.addEventListener("dragover", (e) => { e.preventDefault(); soltar.classList.add("sobre"); });
soltar.addEventListener("dragleave", () => soltar.classList.remove("sobre"));
soltar.addEventListener("drop", (e) => {
  e.preventDefault();
  soltar.classList.remove("sobre");
  $("#arquivo").files = e.dataTransfer.files;
  mostrarArquivo();
  const problema = problemaNoArquivo($("#arquivo").files[0]);
  if (problema) avisar(problema);
});
$("#form-documento").addEventListener("submit", (e) => {
  e.preventDefault();
  const arquivo = $("#arquivo").files[0];
  const problema = problemaNoArquivo(arquivo);
  if (problema) return avisar(problema);
  const form = new FormData();
  form.append("arquivo", arquivo);
  form.append("titulo", $("#titulo").value);
  form.append("fonte", $("#fonte").value);
  tentar(async () => {
    await comecar("/conhecimento", { form });
    e.target.reset();
    mostrarArquivo();
  });
});
$("#zip-manual").addEventListener("change", (e) => {
  const arquivo = e.target.files[0];
  e.target.value = "";
  if (arquivo) tentar(() => comecar("/manual", { form: formManual({ arquivo }) }));
});

$("#f-modo").append(...MODOS.map((m) => new Option(m, m)));
$("#form-filtros").addEventListener("submit", (e) => {
  e.preventDefault();
  tentar(carregarHistorico);
});
$("#exportar").addEventListener("click", () => tentar(exportar));

for (const campo of ["#exp-repeticoes", "#exp-temperatura"]) $(campo).addEventListener("input", previstas);
$("#form-experimento").addEventListener("submit", (e) => {
  e.preventDefault();
  tentar(() => comecar("/experimento/iniciar", { json: {
    modelos: [...escolhidos.modelos], tarefas: [...escolhidos.tarefas], condicoes: [...escolhidos.condicoes],
    repeticoes: parseInt($("#exp-repeticoes").value, 10) || 1, temperatura: parseFloat($("#exp-temperatura").value), seeds: seeds(),
  } }));
});
$("#cena-base").addEventListener("click", () => tentar(async () => {
  await postar("/experimento/cena-base");
  avisar("Cena base salva.");
  await carregar();
}));
$("#parar").addEventListener("click", () => tentar(async () => {
  await postar("/experimento/parar");
  avisar("Parando: a execução atual termina e o que já foi gravado fica salvo.");
}));
$("#verificar").addEventListener("click", carregar);

window.addEventListener("hashchange", () => {
  abrir();
  $("#pagina").focus({ preventScroll: true }); // leitor de tela e Tab começam pela tela nova, não pela lateral
});
abrir();
situacao();
setInterval(situacao, 15000);
vigiar();
