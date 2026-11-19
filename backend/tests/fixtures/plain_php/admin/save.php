<?php
require '../lib/db.php';
$name = $_POST['name'];
fetch_rows($name);
