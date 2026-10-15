import { Compass } from "lucide-react";
import { Link } from "react-router";
import { PageBody } from "@/components/layout/PageHeader";
import { buttonClasses } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";

export default function NotFoundPage() {
  return (
    <PageBody className="flex flex-1 items-center justify-center">
      <EmptyState
        className="w-full max-w-lg"
        icon={<Compass />}
        title="Page not found"
        description="The page you are looking for does not exist or has moved."
        action={
          <Link to="/" className={buttonClasses()}>
            Go to the marketplace
          </Link>
        }
      />
    </PageBody>
  );
}
