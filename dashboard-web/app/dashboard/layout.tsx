"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth-context";

const navigation = [
  { name: "Dashboard", href: "/dashboard" },
  { name: "Sources", href: "/sources" },
  { name: "Library", href: "/library" },
  { name: "Ask", href: "/ask" },
  { name: "Briefings", href: "/briefings" },
  { name: "Schedules", href: "/schedules" },
  { name: "Settings", href: "/settings" },
];

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const { user, signOut } = useAuth();

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="flex">
        {/* Sidebar */}
        <div className="w-64 bg-white border-r border-gray-200 min-h-screen">
          <div className="p-6">
            <h1 className="text-xl font-bold text-gray-900">SourceHero AI</h1>
          </div>
          <nav className="px-4 space-y-1">
            {navigation.map((item) => (
              <Link
                key={item.name}
                href={item.href}
                className={`block px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                  pathname === item.href
                    ? "bg-blue-50 text-blue-700"
                    : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
                }`}
              >
                {item.name}
              </Link>
            ))}
          </nav>
          <div className="absolute bottom-0 w-64 p-4 border-t border-gray-200">
            <div className="text-sm text-gray-500 truncate">{user?.email}</div>
            <button
              onClick={signOut}
              className="mt-2 text-sm text-red-600 hover:text-red-700"
            >
              Sign Out
            </button>
          </div>
        </div>

        {/* Main content */}
        <div className="flex-1 p-8">{children}</div>
      </div>
    </div>
  );
}
