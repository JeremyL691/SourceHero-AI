import Link from "next/link";

export default function Home() {
  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center">
      <div className="max-w-md w-full text-center space-y-8">
        <div>
          <h1 className="text-4xl font-bold text-gray-900">SourceHero AI</h1>
          <p className="mt-4 text-lg text-gray-600">
            Cloud-hosted knowledge base for cited retrieval over RSS, webpages, and PDFs.
          </p>
        </div>
        <div className="space-y-4">
          <Link
            href="/login"
            className="block w-full py-3 px-4 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 transition-colors"
          >
            Sign In
          </Link>
          <Link
            href="/register"
            className="block w-full py-3 px-4 border border-gray-300 text-gray-700 rounded-lg font-medium hover:bg-gray-50 transition-colors"
          >
            Create Account
          </Link>
        </div>
      </div>
    </div>
  );
}
