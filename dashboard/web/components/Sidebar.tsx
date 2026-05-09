"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navItems = [
  { href: "/overview", label: "📊 Overview" },
  { href: "/logs", label: "📋 Audit Logs" },
  { href: "/moderation", label: "🔨 Moderation" },
  { href: "/queues", label: "🎮 Queues" },
  { href: "/suggestions", label: "💡 Suggestions" },
];

export default function Sidebar() {
  const pathname = usePathname();

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
      <div className="px-4 py-4 border-t border-slate-700 flex items-center gap-3">
        <div className="w-8 h-8 rounded-full bg-purple-700 flex items-center justify-center text-white text-sm font-bold shrink-0">
          A
        </div>
        <span className="text-slate-300 text-sm font-medium">Admin</span>
      </div>
    </aside>
  );
}
