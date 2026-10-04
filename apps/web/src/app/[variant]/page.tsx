import { notFound } from "next/navigation";
import { Onboarding } from "@/components/onboarding";
import { VARIANTS, type Variant } from "@/lib/contracts";

export default async function VariantPage({ params }: { params: Promise<{ variant: string }> }) {
  const { variant } = await params;
  if (!VARIANTS.includes(variant as Variant)) notFound();
  return <Onboarding variant={variant as Variant} />;
}
