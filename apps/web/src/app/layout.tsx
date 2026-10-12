import type { Metadata } from "next";
import "@fontsource/manrope/400.css";
import "@fontsource/manrope/500.css";
import "@fontsource/manrope/600.css";
import "@fontsource/manrope/700.css";
import "@fontsource/manrope/800.css";
import "@fontsource/unbounded/500.css";
import "@fontsource/unbounded/700.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "rubai — API к моделям OpenRouter и веб-агенты",
  description: "OpenAI-совместимый API с оплатой с баланса платформы или собственным ключом OpenRouter. Веб-агенты Hermes и Pi.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ru"><body>{children}</body></html>;
}
