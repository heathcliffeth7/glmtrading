-- Migration: Add exit_plan_history column to trades table
-- Date: 2025-11-04
-- Description: Adds JSON column to track all exit plan updates for dynamic exit plan management

-- Add exit_plan_history column if not exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'trades' 
        AND column_name = 'exit_plan_history'
    ) THEN
        ALTER TABLE trades 
        ADD COLUMN exit_plan_history JSON DEFAULT NULL;
        
        COMMENT ON COLUMN trades.exit_plan_history IS 'History of all exit plan updates: {"updates": [...]}';
        
        RAISE NOTICE 'Column exit_plan_history added successfully';
    ELSE
        RAISE NOTICE 'Column exit_plan_history already exists, skipping';
    END IF;
END $$;

-- Initialize existing trades with empty history
UPDATE trades 
SET exit_plan_history = '{"updates": []}'::json
WHERE exit_plan_history IS NULL 
  AND close_price IS NULL;  -- Only for open positions
