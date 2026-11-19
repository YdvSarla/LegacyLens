<?php
function format_rows($rows) {
    $out = '';
    foreach ($rows as $row) {
        if ($row && isset($row['name'])) {
            $out .= $row['name'];
        } elseif ($row) {
            $out .= 'unknown';
        }
    }
    return $out;
}

class Legacy_Report {
    public function render($rows) {
        return format_rows($rows);
    }
}
