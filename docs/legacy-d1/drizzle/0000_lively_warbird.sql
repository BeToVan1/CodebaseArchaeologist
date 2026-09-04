CREATE TABLE `deep_admissions` (
	`id` text PRIMARY KEY NOT NULL,
	`client_key` text NOT NULL,
	`created_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `idx_deep_admissions_created_at` ON `deep_admissions` (`created_at`);--> statement-breakpoint
CREATE INDEX `idx_deep_admissions_client_created_at` ON `deep_admissions` (`client_key`,`created_at`);
