"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";

const navItems = [
  { href: "/overview", label: "📊 Overview" },
  { href: "/logs", label: "📋 Audit Logs" },
  { href: "/moderation", label: "🔨 Moderation" },
  { href: "/queues", label: "🎮 Queues" },
  { href: "/suggestions", label: "💡 Suggestions" },
  { href: "/voice", label: "🎙️ Voice" },
  { href: "/birthdays", label: "🎂 Birthdays" },
  { href: "/music", label: "🎵 Music" },
];

interface User {
  id: string;
  username: string;
  avatar: string | null;
}

function avatarUrl(user: User): string | null {
  if (!user.avatar) return null;
  return `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png?size=64`;
}

export default function Sidebar() {
  const pathname = usePathname();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    fetch("/auth/me", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: User | null) => setUser(data))
      .catch(() => setUser(null));
  }, []);

  async function logout() {
    await fetch("/auth/logout", { method: "POST", credentials: "include" });
    window.location.href = "/auth/login";
  }

  return (
    <aside className="w-48 min-h-screen bg-slate-800 flex flex-col shrink-0">
      {/* Header */}
      <div className="px-4 py-5 border-b border-slate-700">
        <span className="text-purple-400 font-bold text-lg tracking-tight">
          ⚙ Bot Admin
        </span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-4 flex flex-col gap-1 px-2">
        {navItems.map(({ href, label }) => {
          const isActive = pathname === href || pathname.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-2 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                isActive
                  ? "bg-purple-900/30 text-white"
                  : "text-slate-400 hover:text-white hover:bg-slate-700"
              }`}
            >
              {label}
            </Link>
          );
        })}
      </nav>

      {/* Footer / User */}
      <div className="px-4 py-4 border-t border-slate-700">
        {user ? (
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2 min-w-0">
              {avatarUrl(user) ? (
                <img
                  src={avatarUrl(user)!}
                  alt={user.username}
                  className="w-8 h-8 rounded-full shrink-0"
                />
              ) : (
                <div className="w-8 h-8 rounded-full bg-purple-700 flex items-center justify-center text-white text-sm font-bold shrink-0">
                  {user.username[0].toUpperCase()}
                </div>
              )}
              <span className="text-slate-300 text-sm font-medium truncate">
                {user.username}
              </span>
            </div>
            <button
              onClick={logout}
              className="text-xs text-slate-500 hover:text-slate-300 transition-colors text-left"
            >
              Log out
            </button>
          </div>
        ) : (
          <a
            href="/auth/login"
            className="text-sm text-purple-400 hover:text-purple-300 transition-colors"
          >
            Log in with Discord →
          </a>
        )}
      </div>
    </aside>
  );
}
