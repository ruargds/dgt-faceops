import React from "react";

/**
 * Rede de proteção: erro de render numa tela não pode apagar o painel.
 *
 * Existe por um caso concreto (01/09/2026): uma variável fora de escopo em
 * `DispositivosView` levantou `ReferenceError` durante o render. Sem
 * fronteira de erro, o React desmonta a árvore INTEIRA — e o que a pessoa
 * vê é uma tela preta, sem menu, sem mensagem e sem pista do que fazer.
 * Recarregar não resolvia, porque o erro voltava no mesmo ponto.
 *
 * Com esta fronteira, o estrago fica na tela que quebrou: o menu continua
 * de pé, dá para ir para outra aba, e a mensagem diz o que aconteceu.
 *
 * `key` na fronteira (a aba ativa) faz o estado de erro sumir ao trocar de
 * tela — sem isso, uma tela quebrada deixaria a fronteira presa em erro
 * mesmo depois de navegar para outra.
 */
export default class LimiteDeErro extends React.Component {
  constructor(props) {
    super(props);
    this.state = { erro: null };
  }

  static getDerivedStateFromError(erro) {
    return { erro };
  }

  componentDidCatch(erro, info) {
    // Vai para o console do navegador, que é onde alguém investigando um
    // "sumiu tudo" olha primeiro.
    console.error("Erro ao desenhar a tela:", erro, info);
  }

  render() {
    if (!this.state.erro) return this.props.children;

    return (
      <div className="card" style={{ borderColor: "var(--red-bd)" }}>
        <div className="section-title" style={{ color: "var(--red-fg)" }}>
          Esta tela falhou ao desenhar
        </div>
        <div className="small muted" style={{ marginBottom: 10 }}>
          O resto do painel continua funcionando — use o menu para ir a outra
          tela. Se o erro repetir, ele está anotado no console do navegador
          (F12) e vale registrar em Erros conhecidos.
        </div>
        <pre
          className="mono small"
          style={{
            background: "var(--bg-2)", padding: 10, borderRadius: "var(--radius)",
            whiteSpace: "pre-wrap", margin: 0, color: "var(--red-fg)",
          }}
        >
          {String(this.state.erro && this.state.erro.message ? this.state.erro.message : this.state.erro)}
        </pre>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          style={{ marginTop: 10 }}
          onClick={() => this.setState({ erro: null })}
        >
          Tentar desenhar de novo
        </button>
      </div>
    );
  }
}
