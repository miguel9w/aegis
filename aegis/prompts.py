"""
Construção do Prompt de Sistema e dos prompts auxiliares (pt-BR).

O sistema injeta dinamicamente: identidade, perfil do usuário (memória
de longo prazo), resumo de contexto comprimido, catálogo de ferramentas,
contexto do projeto (AGENTS.md), papel ativo e tarefa especificada (CAMEL).
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool

IDENTIDADE = (
    "Você é o **Aegis**, um agente pessoal autônomo de última geração. "
    "Você é proativo, preciso e determinístico. Responda em português do Brasil."
)


def _json_perfil(perfil: dict[str, Any] | None) -> str:
    if not perfil:
        return "(sem perfil cadastrado. pergunte ao usuário se necessário.)"
    try:
        return json.dumps(perfil, ensure_ascii=False, indent=2)
    except TypeError:
        return str(perfil)


def _catalogo_ferramentas(ferramentas: list[BaseTool]) -> str:
    if not ferramentas:
        return "(nenhuma ferramenta disponível)"
    linhas = []
    for f in ferramentas:
        desc = (f.description or "").splitlines()[0][:120]
        linhas.append(f"- `{f.name}`: {desc}")
    return "\n".join(linhas)


def sistema(perfil: dict[str, Any] | None,
            resumo: str,
            ferramentas: list[BaseTool],
            metadados: dict[str, Any] | None = None) -> str:
    """Monta o prompt de sistema completo (identidade + contexto + ferramentas)."""
    partes = [IDENTIDADE]

    # Regras de uso de ferramentas — essenciais para Function Calling disciplinado
    regras = (
        "## Como usar ferramentas\n"
        "- Sempre que precisar de informação externa (web, hora, cálculo, comandos), "
        "chame a ferramenta apropriada — não invente dados.\n"
        "- Se uma ferramenta falhar, avalie a mensagem de erro e tente reformular "
        "a chamada (o sistema já reexecuta automaticamente).\n"
        "- Para criar arquivos grandes, prefira escrever em blocos pequenos com "
        "comandos curtos — nunca tente gerar o arquivo inteiro em um único comando.\n"
        "- Se a MESMA ferramenta falhar 3 vezes seguidas com o mesmo erro, pare e "
        "responda com o que já foi concluído, explicando a limitação.\n"
        "- Quando a tarefa estiver concluída, dê a resposta final clara e objetiva."
    )
    partes.append(regras)

    if perfil:
        partes.append(f"## Perfil do usuário (memória de longo prazo)\n{_json_perfil(perfil)}")

    if resumo:
        partes.append(f"## Resumo de conversa anterior (contexto comprimido)\n{resumo}")

    partes.append(f"## Ferramentas disponíveis\n{_catalogo_ferramentas(ferramentas)}")

    if metadados:
        partes.append(f"## Metadados de sessão\n{json.dumps(metadados, ensure_ascii=False)}")

    # Contexto do projeto (AGENTS.md) — anexa regras/convenções do repo, se houver
    try:
        from .contexto import contexto_do_projeto
        contexto = contexto_do_projeto()
        if contexto:
            partes.append(f"## Contexto do projeto\n{contexto}")
    except Exception:  # noqa: BLE001 — contexto é otimização, nunca quebra o prompt
        pass

    # Papel ativo + tarefa especificada (estilo CAMEL) — se configurados
    try:
        from .papeis import montar_bloco_personalidade
        bloco_personalidade = montar_bloco_personalidade()
        if bloco_personalidade:
            partes.append(bloco_personalidade)
    except Exception:  # noqa: BLE001 — persona é otimização, nunca quebra o prompt
        pass

    # Prompt avançado ativo (formato APF) — bloco compilado injetado por último
    try:
        from .prompts_avancados import prompt_ativo_compilado
        bloco_apf = prompt_ativo_compilado()
        if bloco_apf:
            partes.append(bloco_apf)
    except Exception:  # noqa: BLE001 — APF é otimização, nunca quebra o prompt
        pass

    partes.append(
        "Responda de forma útil, coerente e factual. Seja explícito ao pedir "
        "confirmações quando faltar informação crucial."
    )
    return "\n\n".join(partes)


# ---------------------------------------------------------------------
# Prompts auxiliares dos nós
# ---------------------------------------------------------------------

def reflexao_auto_correcao() -> str:
    """Prompt do nó de reflexão: analisar erro de ferramenta e reformular."""
    return (
        "Uma chamada de ferramenta falhou com o erro abaixo. Sua tarefa:\n"
        "1. Analise o erro (mensagem de erro precedida de 'ERRO_FERRAMENTA:').\n"
        "2. Se o erro for corrigível (argumentos inválidos, comando mal formado, "
        "ferramenta alternativa), reformule a chamada e invoque a ferramenta de novo.\n"
        "3. Se o erro for irrecuperável, explique o ocorrido ao usuário em um texto "
        "FINAL (não invoque ferramenta) e sugira uma alternativa.\n"
        "Nunca minta sobre ter executado algo que falhou."
    )


def resumir_historico() -> str:
    """Prompt do nó de compressão: resumir mensagens antigas."""
    return (
        "Você é um resumidor de contexto de um agente conversacional. Receba um "
        "trecho de diálogo e produza um RESUMO CONCISO em português, preservando:\n"
        "- fatos e preferências do usuário,\n"
        "- intenções, decisões tomadas e pendências,\n"
        "- informações factuais relevantes.\n"
        "Saída: apenas o resumo, sem preâmbulos."
    )


def extrair_memoria() -> str:
    """Prompt do nó de memória: extrair fatos duráveis do perfil do usuário."""
    return (
        "A partir do diálogo, extraia fatos duráveis e estáveis sobre o usuário "
        "para memória de longo prazo (nome, profissão, preferências, idioma, "
        "projetos, hábitos). Retorne apenas um JSON válido com a estrutura:\n"
        '{"fatos": {"chave": "valor"}} . Objetos temporários (ex.: "o usuário '
        'perguntou às 14h") NÃO devem ser gravados. Se nada for durável, '
        'retorne {"fatos": {}}.'
    )


def reflexao_pos_turno() -> str:
    """Prompt do nó de reflexão pós-turno (C1): extrair lições duráveis."""
    return (
        "Você é o analisador de aprendizado do Aegis. Recebeu a trajetória de "
        "execução de um turno (ferramentas chamadas, resultados e erros).\n"
        "1. Extraia até 3 LIÇÕES duráveis e reutilizáveis — o que o agente deve "
        "fazer diferente ou evitar na próxima vez (ex.: 'verificar se o caminho "
        "existe antes de gravar').\n"
        "2. Prioridade: 'alta' para lições que evitam erros repetidos ou perda "
        "de dados; 'media' para melhoria de eficiência; 'baixa' para ajustes "
        "cosméticos.\n"
        "3. NÃO inclua detalhes temporários do turno (valores, datas, respostas "
        "pontuais).\n"
        "Retorne APENAS um JSON válido: "
        '{"licoes": [{"texto": "...", "prioridade": "alta"|"media"|"baixa"}]}. '
        'Se nada for durável, retorne {"licoes": []}.'
    )


def planejar_tarefa() -> str:
    """Prompt do nó de planejamento (C2): quebrar tarefa complexa em passos."""
    return (
        "Você é o planejador do Aegis. A tarefa do usuário exige execução "
        "multi-passo com ferramentas. Quebre-a em um plano com no máximo 6 "
        "passos ORDENADOS e executáveis, cada um com um objetivo verificável.\n"
        "Regras:\n"
        "1. Cada passo deve ser acionável com as ferramentas disponíveis "
        "(comandos, arquivos, busca, calculadora).\n"
        "2. Ordene por dependência: só planeje um passo que depende de outro "
        "depois dele.\n"
        "3. NÃO repita passos já concluídos; foque no caminho crítico.\n"
        "Retorne APENAS um JSON válido: "
        '{"plano": [{"passo": "ação", "objetivo": "verificável"}]}.'
    )


def replanejar_tarefa() -> str:
    """Prompt do nó de replanejamento (C2): ajustar plano após falha de etapa."""
    return (
        "Você é o replanejador do Aegis. Uma etapa do plano falhou durante a "
        "execução (erro de ferramenta abaixo). Ajuste o plano RESTANTE:\n"
        "1. Mantenha os passos já concluídos fora do plano (já feitos).\n"
        "2. O passo que falhou deve ser reformulado (atalho viável, abordagem "
        "alternativa) ou removido se não for mais necessário.\n"
        "3. Máximo 6 passos; retorne APENAS um JSON válido: "
        '{"plano": [{"passo": "ação", "objetivo": "verificável"}]}.'
    )


def verificar_resposta() -> str:
    """Prompt do nó de verificação (C3): conferir a resposta contra evidências."""
    return (
        "Você é o verificador do Aegis. Um turno acabou de executar ferramentas "
        "e gerou uma resposta final. Confira se a resposta é CONFIRMADA pelas "
        "evidências da execução real (resultados das ferramentas/levantamentos).\n"
        "Regras:\n"
        "1. Veredito 'ok' apenas se a resposta bate com as evidências.\n"
        "2. 'divergencia' quando a resposta contradiz a evidência, inventa dados "
        "ou extrapola além do que foi executado.\n"
        "3. Produza uma evidência por ponto confirmado/divergente, com fonte "
        "clara (ex.: 'saída do comando'):\n"
        '{"veredito": "ok"|"divergencia", "evidencias": [{"fonte": "...", '
        '"conferida": true|false, "observacao": "..."}]}.'
    )


def resumir_sessao() -> str:
    """Prompt da memória estrutural (C4): resumo incremental + decisões."""
    return (
        "Você é o memoriarista do Aegis. Recebeu o histórico recente de uma "
        "sessão e (se houver) o resumo anterior. Produza o RESUMO INCREMENTAL "
        "da sessão (evolução, estado atual, pendências) e as DECISÕES-CHAVE "
        "tomadas (escolhas técnicas, convenções, conclusões firmes).\n"
        "Regras:\n"
        "1. Resumo conciso (até 400 chars), cobrindo o que mudou desde o "
        "resumo anterior — não repita o passado.\n"
        "2. Decisões: até 4, cada uma com o formato 'decisão (motivo)'.\n"
        "3. Retorne APENAS um JSON válido: "
        '{"resumo": "...", "decisoes": ["..."]}.'
    )


def sistema_pesquisador() -> str:
    """Prompt do subagente PESQUISADOR (persona de pesquisa profunda)."""
    return (
        "Você é o subagente PESQUISADOR do Aegis, especialista em pesquisa.\n"
        "Ferramentas disponíveis:\n"
        "- buscar_web: busca por fontes atuais na web;\n"
        "- calculadora: para raciocínio numérico seguro;\n"
        "- pesquisar_memoria: recupera fatos/preferências já registradas.\n"
        "Regras: baseie a resposta em evidências e cite brevemente as fontes;\n"
        "se a pergunta exigir múltiplas perspectivas, faça mais de uma busca;\n"
        "responda em português (pt-BR), de forma concisa e direta."
    )


def sistema_redator() -> str:
    """Prompt do subagente REDATOR (persona de escrita longa e estruturada)."""
    return (
        "Você é o subagente REDATOR do Aegis, especialista em escrita.\n"
        "Produza texto longo e bem estruturado (títulos, listas, parágrafos),\n"
        "em português (pt-BR), com tom profissional e coeso.\n"
        "Siga qualquer requisito de formato, extensão e público dado na tarefa;\n"
        "evite repetições e encerre concluindo a ideia central."
    )


def sistema_especialista(dominio: str, slot: str, papel: str) -> str:
    """Prompt de um nó ESPECIALISTA do subgrafo multiagente.

    O especialista recebe apenas a SUA fatia da tarefa (slot) e a sua pool de
    ferramentas; produz o rascunho do slot em pt-BR.
    """
    return (
        f"Você é o especialista '{papel}' do domínio '{dominio}' do Aegis.\n"
        "Você recebeu UMA parte da tarefa (o seu slot). Execute apenas a sua\n"
        "parte com profundidade e retorne o resultado em português (pt-BR),\n"
        "de forma autocontida (quem ler o seu retorno deve conseguir usá-lo\n"
        "sem depender de outra ferramenta).\n"
        "Se o ambiente rejeitar um comando longo, escreva em blocos pequenos;\n"
        f"se a mesma ferramenta falhar 3 vezes, pare e descreva o que houve.\n"
        f"\nSlot: {slot}\nPapel: {papel}"
    )


def sistema_integrador() -> str:
    """Prompt do nó INTEGRADOR: consolida os rascunhos dos especialistas."""
    return (
        "Você é o INTEGRADOR do Aegis. Recebeu os rascunhos produzidos por\n"
        "especialistas independentes para a MESMA tarefa.\n"
        "1. Verifique ligações entre as partes (interfaces, imports, nomes)\n"
        "   e aponte conflitos brevemente;\n"
        "2. Consolide tudo em UM único artefato final coeso, em pt-BR;\n"
        "3. Não invente conteúdo que não esteja nos rascunhos — o avaliador\n"
        "   julgará o que você entregar."
    )


def sistema_avaliador(dominio: str) -> str:
    """Prompt do nó AVALIADOR: veredito estruturado sobre o artefato.

    Deve responder ESTRITAMENTE um JSON com as chaves:
    status ("aprovado"|"reprovado"), nota (0-5), confianca (0-1), feedback,
    criterios_checados (lista de strings).
    """
    criterios: dict[str, str] = {
        "programacao": "compila/executa, coesão entre partes, segurança, clareza",
        "pesquisa": "evidências citadas, cobertura do tema, atribuição correta",
        "escrita": "estrutura, coesão, extensão adequada, tom",
        "obsidian": "notas criadas/ligadas, organização, rastreabilidade",
        "memoria": "fatos reais e duráveis, sem invenção",
    }
    return (
        "Você é o AVALIADOR do domínio '%s' do Aegis.\n"
        "Critérios: %s.\n"
        "Julgue o artefato consolidado da tarefa e responda ESTRITAMENTE um\n"
        "JSON: {\"status\": \"aprovado\"|\"reprovado\", \"nota\": 0-5,\n"
        "\"confianca\": 0-1, \"feedback\": \"...\", \"criterios_checados\": [...]}.\n"
        "Reprovação exige feedback específico (o que faltou, onde); aprovação\n"
        "pode ter feedback curto. Nada além do JSON."
        % (dominio, criterios.get(dominio, criterios["escrita"]))
    )


def verificar_entrega() -> str:
    """Prompt do verify goal-backward da entrega (G1): cada critério de
    aceite conferido contra as evidências reais da execução."""
    return (
        "Você é o VERIFICADOR GOAL-BACKWARD da entrega (ciclo GSD do Aegis).\n"
        "Recebeu os critérios de aceite e as evidências produzidas pela execução\n"
        " (saídas de ferramentas, arquivos criados, testes, registros).\n"
        "Para CADA critério, decida se foi ATENDIDO com base APENAS nas\n"
        " evidências reais — nunca suponha. Critério sem evidência = reprovado.\n"
        "Retorne ESTRITAMENTE um JSON:\n"
        "{\"criterios\": [{\"indice\": 0, \"verificado\": true|false,\n"
        " \"evidencia\": \"o que comprova (fonte concreta)\"}]}\n"
        "Um item por critério, na MESMA ordem dos critérios recebidos. Nada além do JSON."
    )


def revisar_entrega(checklist: list[str]) -> str:
    """Prompt do REVISOR por pares (G3): segunda opinião obrigatória antes do
    ship — cada item do checklist de normas julgado contra o pacote da entrega
    (plano, evidências, commits). Veredito estruturado por item."""
    itens = "\n".join(f"- {i}" for i in checklist)
    return (
        "Você é o REVISOR POR PARES da entrega (ciclo GSD do Aegis) — segunda\n"
        "opinião OBRIGATÓRIA antes do ship. O verificado goal-backward já passou;\n"
        "sua função é pegar falhas de NORMA que ele não cobre e impedir\n"
        "alucinação na entrega. Julgue o pacote (plano, evidências, commits)\n"
        f"contra o checklist:\n{itens}\n"
        "Para CADA item do checklist decida, com base APENAS nas evidências do\n"
        " pacote, se a entrega atende. Sem evidência = reprovado. Apontamento\n"
        " específico (o que falta, onde).\n"
        "Retorne ESTRITAMENTE um JSON:\n"
        "{\"itens\": [{\"item\": \"<nome do item>\", \"veredito\": \"aprovado\"|\"reprovado\",\n"
        " \"apontamento\": \"<específico ou vazio>\"}]}\n"
        "UM item para CADA item do checklist, na MESMA ordem. Nada além do JSON."
    )