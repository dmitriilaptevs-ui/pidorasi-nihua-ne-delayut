export const VARIANTS = ["canvas"] as const;
export type Variant = (typeof VARIANTS)[number];
export type AuthProvider = "vk" | "yandex";
/** How the account was created; also used to label the signed-in viewer. */
export type SignupMethod = AuthProvider | "password";

export interface ModelOption {
  id: string;
  name: string;
  provider: string;
  contextLength: number;
}

export interface Viewer {
  name: string;
  provider: SignupMethod;
}

export interface IntegrationStatus {
  providers: Record<AuthProvider, boolean>;
  inference: boolean;
}

export interface GenerationResult {
  text: string;
  model: string;
  requestId: string;
  inputTokens: number | null;
  outputTokens: number | null;
}

export interface ExperienceProps {
  variant: Variant;
  viewer: Viewer | null;
  status: IntegrationStatus;
  models: ModelOption[];
  modelsLoading: boolean;
  modelsError: string | null;
  selectedModel: string;
  onSelectModel: (id: string) => void;
  onRefreshModels: () => void;
  onSignIn: (provider: AuthProvider) => void;
  onSignOut: () => void;
  authPending: boolean;
  authError: string | null;
  prompt: string;
  onPromptChange: (value: string) => void;
  consent: boolean;
  onConsentChange: (value: boolean) => void;
  onGenerate: () => void;
  onCancel: () => void;
  generating: boolean;
  result: GenerationResult | null;
  generationError: string | null;
}
