# AGENTE IA EDU

Core independente para aquisição, organização, classificação e busca inteligente de questões educacionais.

## Objetivo

Construir uma infraestrutura de inteligência educacional capaz de:

- buscar questões automaticamente em fontes oficiais;
- extrair e organizar questões e gabaritos;
- classificar questões por disciplina, assunto e subassunto;
- estimar nível de dificuldade;
- manter banco próprio de questões;
- utilizar bases teóricas por meio de RAG;
- permitir busca e geração inteligente de listas;
- disponibilizar os recursos por API para diferentes plataformas educacionais.

## Arquitetura

O projeto foi concebido para ser independente de uma plataforma específica e de um único fornecedor de inteligência artificial.

Componentes iniciais:

- PostgreSQL
- pgvector
- n8n
- API própria
- workers de processamento
- camada de abstração para provedores de IA

## Princípio

> O AGENTE IA EDU deve sobreviver à troca de qualquer fornecedor de IA.

Os dados, taxonomias, regras, prompts, classificações e conhecimento do sistema devem permanecer independentes dos provedores de inteligência artificial.

## Status

Projeto em desenvolvimento.
