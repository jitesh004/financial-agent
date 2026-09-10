-- A custom category needs somewhere to keep the group it was created with.
--
-- The create endpoint accepted a group, the widget asked for one, and there
-- was no column to put it in - so every user-defined category silently
-- landed in "Other". That is not a cosmetic default: "Other" is one of the
-- five slices on the Overview's cashflow chart, so a category deliberately
-- created as an Essential could never appear as one, and the chart quietly
-- disagreed with the label the user had chosen.
ALTER TABLE custom_categories
    ADD COLUMN IF NOT EXISTS group_name TEXT NOT NULL DEFAULT 'Other';
