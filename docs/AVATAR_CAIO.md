# Avatar do Caio

O rosto oficial escolhido está em `dashboard/src/assets/caio-avatar.png`. É uma cópia local da imagem original, incluída no build do dashboard; a interface não precisa carregar essa imagem pela rede externa. Ele aparece no cabeçalho do chat e, maior, na tela de boas-vindas. O avatar maior tem um movimento leve de flutuação e escala uniforme, desativado com `prefers-reduced-motion: reduce`.

A imagem disponível é uma única imagem estática, sem camadas ou quadros de olhos fechados. Por isso, **o avatar não pisca**: não foram desenhados olhos falsos nem deformada a imagem para simular piscadas. Para adicionar piscadas fiéis no futuro seriam necessários quadros ou camadas apropriadas do mesmo personagem.
