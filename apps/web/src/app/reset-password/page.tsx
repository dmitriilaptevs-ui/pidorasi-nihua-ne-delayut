import { AuthPanel } from "@/components/auth-panel";

export default async function ResetPasswordPage({ searchParams }: { searchParams: Promise<{ token?: string }> }) {
  const { token } = await searchParams;
  return <AuthPanel mode="reset" token={token ?? ""} />;
}
