"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { api, User } from "@/lib/api";

const NAV: [string, string][] = [
  ["Overview", "/dashboard"],
  ["Repositories", "/dashboard/repos"],
  ["Reports", "/dashboard/reports"],
  ["Settings", "/dashboard/settings"],
];

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .me()
      .then((u) => {
        setUser(u);
        setLoading(false);
      })
      .catch(() => router.replace("/"));
  }, [router]);

  function isActive(href: string) {
    return href === "/dashboard"
      ? pathname === href
      : pathname.startsWith(href);
  }

  async function signOut() {
    await api.logout();
    router.replace("/");
  }

  if (loading) return <div className="center">Loading…</div>;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">◆ Bitter</div>
        <nav>
          {NAV.map(([label, href]) => (
            <Link
              key={href}
              href={href}
              className={isActive(href) ? "active" : ""}
            >
              {label}
            </Link>
          ))}
        </nav>
        <div className="user">
          {user?.login}
          <button onClick={signOut}>Sign out</button>
        </div>
      </header>
      <main className="content">{children}</main>
    </div>
  );
}
