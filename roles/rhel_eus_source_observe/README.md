# RHEL EUS: observações da origem

O JT `samurai_rhel_eus_source_observe` executa duas medições independentes:

- `repository`: lê os arquivos do perfil alvo disponível, os hashes revisados do vendor, a identidade física e o estado instalado.
- `eligibility`: faz essas leituras e executa somente `/usr/bin/rhui-eus-switch`, sem argumentos. O ramo revisado retorna a elegibilidade sem ativar EUS.

Um arquivo `.repo.disabled` com o hash autorizado pode provar disponibilidade do perfil alvo. O resultado informa seu caminho, se o DNF atual o carrega e quais repositórios estão ativos. Isso não afirma que a origem regular foi convertida para EUS.

O operador usa a UI Manual com o Asset e a credencial própria. Approval, Change, janela, C6 `manual`, trust atual e validação fresca continuam no caminho existente. O vendor exige UID0: metadata `become` admitida, prova fresca de elevação e réplica efetiva da tentativa devem ser conferidas pelo produto e pelo operador. `measurement_euid=0` registra uma condição operacional; não prova autorização C6. Não há variável de senha, chave ou autorização livre neste producer.

Os inputs identificam a organização, Source, run preparado, execução da coleta, fingerprint e identidade AWS. `HOSTNAMES` vem do Asset governado. O programa recusa outro instance/account/region, versão distinta de RHEL9.6, código vendor diferente e qualquer mudança dos hashes de configuração ou estado instalado durante a leitura.

O artifact `samurai_rhel_eus_observation` contém o JSON completo da medição, seu SHA256 e o SHA256 dos bytes do programa. Duas execuções AAP reais, distintas e com SCM/EE/credencial/trust comprovados sustentam a associação posterior por PR. O resultado mantém `catalog_qualified=false`; não cria binding, não transfere aprovação e não altera M60/P40/M61/P41 ou a autoridade EUS.

Para publicar somente este JT pelo provisionador existente, selecione `aap_job_template_names=[samurai_rhel_eus_source_observe]`. Essa seleção não reaplica o workflow de rotação. O export declara Project, inventory, EE e organização pelos nomes verificados do destino; eles devem ser conferidos antes de aplicar em outro ambiente.
