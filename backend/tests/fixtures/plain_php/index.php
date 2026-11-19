<?php
require_once dirname(__FILE__) . '/lib/db.php';
include 'lib/helpers.php';

$page = $_GET['page'];
echo "<h1>" . $_GET['title'] . "</h1>";
$rows = fetch_rows($page);
print format_rows($rows);
var_dump($rows);
