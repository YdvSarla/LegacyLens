<?php

function legacy_mod_helper_render() {
  $rows = db_query("SELECT * FROM {legacy_items} WHERE id = " . $_GET['id']);
  return theme('table', array('rows' => $rows));
}
