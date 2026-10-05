import { AuthPanel } from "@/components/auth-panel";

export default async function VerifyEmailPage({ searchParams }: { searchParams: Promise<{ token?: string }> }) {
  const { token } = await searchParams;
  return <AuthPanel mode="verify" token={token ?? ""} />;
}
