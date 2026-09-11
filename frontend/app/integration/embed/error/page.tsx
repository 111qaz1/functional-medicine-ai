export default function EmbedErrorPage() {
  return (
    <main className="workflow-app workflow-auth" data-embedded="true">
      <section className="workflow-auth__card">
        <p className="workflow-auth__eyebrow">AI 功能医学辅助</p>
        <h1>嵌入会话无法打开</h1>
        <p>会话可能已过期或已使用。请返回开方页并重新加载 AI 辅助区域；手工开方不受影响。</p>
      </section>
    </main>
  );
}
