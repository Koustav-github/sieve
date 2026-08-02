import { getHealth } from "@/lib/api";

export default async function Home() {
  let status: string;
  try {
    const health = await getHealth();
    status = health.status;
  } catch {
    status = "unreachable";
  }

  return (
    <div className="flex flex-col flex-1 items-center justify-center gap-4 bg-zinc-50 font-sans dark:bg-black">
      <h1 className="text-3xl font-semibold text-black dark:text-zinc-50">
        Sieve
      </h1>
      <p className="text-lg text-zinc-600 dark:text-zinc-400">
        Backend status: <span data-testid="backend-status">{status}</span>
      </p>
    </div>
  );
}
