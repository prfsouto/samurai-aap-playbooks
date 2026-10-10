# RHEL EUS: observações da origem

O JT `samurai_rhel_eus_source_observe` executa duas medições independentes:

- `repository`: lê os arquivos do perfil alvo disponível, os hashes revisados do vendor, a identidade física e o estado instalado.
- `eligibility`: faz essas leituras e executa somente `/usr/bin/rhui-eus-switch`, sem argumentos. O ramo revisado retorna a elegibilidade sem ativar EUS.

Um arquivo `.repo.disabled` com o hash autorizado pode provar disponibilidade do perfil alvo. O resultado informa o caminho, a correspondência com o glob padrão e os IDs configurados como enabled. O estado efetivo DNF fica `not_measured`; esta leitura estática não afirma ativação ou conversão da origem.

O operador usa a UI Manual com o Asset e a credencial própria. Approval, Change, janela, C6 `manual`, trust atual e validação fresca continuam no caminho existente. O vendor exige UID0: metadata `become` admitida, prova fresca de elevação e réplica efetiva da tentativa devem ser conferidas pelo produto e pelo operador. `measurement_euid=0` registra uma condição operacional; não prova autorização C6. Não há variável de senha, chave ou autorização livre neste producer.

Os inputs identificam a organização, Source, run preparado, execução da coleta, fingerprint e identidade AWS. `HOSTNAMES` vem do Asset governado. Antes de conectar, o Controller verifica um alvo único SSH22 e recusa aliases de redirecionamento e variáveis que sobrescrevam usuário/chave/execução da Machine. Chaves internas do collector não podem vir de extra_vars. O alvo não precisa existir previamente no inventário.

A identidade física é verificada sem become antes da etapa root. O programa recusa outro instance/account/region, versão distinta de RHEL9.6, código vendor diferente e qualquer mudança dos hashes de configuração ou estado instalado durante a leitura. O envelope final compara Source inteiro e identidade física com essa leitura prévia.

O artifact `samurai_rhel_eus_observation` contém o JSON completo da medição, seu SHA256 e o SHA256 dos bytes do programa. Duas execuções AAP reais, distintas e com SCM/EE/credencial/trust comprovados sustentam a associação posterior por PR. O resultado mantém `catalog_qualified=false`; não cria binding, não transfere aprovação e não altera M60/P40/M61/P41 ou a autoridade EUS.

Para publicar somente este JT pelo provisionador existente, selecione `aap_job_template_names=[samurai_rhel_eus_source_observe]`. Essa seleção não reaplica o workflow de rotação. O export declara Project, inventory, EE e organização pelos nomes verificados do destino; eles devem ser conferidos antes de aplicar em outro ambiente.
