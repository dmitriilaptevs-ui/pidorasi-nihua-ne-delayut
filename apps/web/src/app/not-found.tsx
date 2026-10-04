import Link from "next/link";

export default function NotFound() {
  return <main style={{ maxWidth: 600, margin: "15vh auto", padding: 24 }}>
    <p>rubai / 404</p><h1>Такой страницы пока нет</h1>
    <p>Вернитесь к выбору модели и первому запросу.</p><Link href="/flow">На главную →</Link>
  </main>;
}
