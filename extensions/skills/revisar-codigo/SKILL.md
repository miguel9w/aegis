---
name: revisar-codigo
description: Checklist e procedimento para revisar código antes de entregar — legibilidade, erros óbvios, segurança e testes.
gatilho: antes de entregar código, ao revisar PR/diff, ou quando pedirem "revise o código"
---

# Revisão de Código

Use esta habilidade antes de entregar qualquer código (próprio ou de
terceiros). Seja objetivo: aponte o problema, onde está e a correção.

## Procedimento

1. **Legibilidade**: nomes claros, funções pequenas, sem duplicação óbvia.
2. **Erros óbvios**: off-by-one, mutação inesperada, exceção engolida,
   comparação com `==` onde deveria ser `is None`.
3. **Segurança**: entrada externa validada, sem SQL/command injection,
   sem segredo em log ou hardcoded.
4. **Testes**: o código novo tem teste? O teste falha se o bug voltar?
5. **Feedback**: liste no formato `[arquivo:linha] problema → correção`.
   Separe *bloqueadores* (quebram a entrega) de *sugestões* (melhorias).

## Saída

- Veredito: `aprovado` ou `precisa_correcao`.
- Lista numerada de apontamentos com localização precisa.
