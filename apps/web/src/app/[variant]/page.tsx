import { redirect } from "next/navigation";

/** The former lab route now lives at "/". */
export default function VariantPage() {
  redirect("/");
}
